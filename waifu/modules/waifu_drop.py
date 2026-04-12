"""
modules/waifu_drop.py

Core game loop:
  - asyncio timer per group → character drop every N minutes
  - /guess to claim
  - /fav to favourite
  - Anti-spam (10 consecutive messages from same user → 10-min ignore)
  - Level-up broadcast to all registered groups
"""
import asyncio
import math
import random
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackContext, CommandHandler, ConversationHandler,
    MessageHandler, filters,
)

from waifu import (
    application, collection, group_user_totals_collection,
    top_global_groups_collection, user_collection, user_totals_collection,
    bot_settings_collection,
    LOGGER, OWNER_ID, sudo_users,
)
# ── Conversation state ────────────────────────────────────────────────────────
_WAIT_ANNOUNCE = 0

# ── Per-chat in-memory state ──────────────────────────────────────────────────
_active_char:      dict[int, dict]      = {}   # chat_id → active character
_claimers:         dict[int, set]       = {}   # chat_id → set of user_ids who claimed
_last_user:        dict[int, dict]      = {}   # chat_id → {user_id, count}
_warned:           dict[int, float]     = {}   # user_id → timestamp of last warning
_sent_ids:         dict[int, list]      = {}   # rolling window of sent char IDs
_registered_chats: set[int]            = set() # all groups ever seen
_msg_count:        dict[int, int]      = {}   # chat_id → messages since last drop
_drop_msg:         dict[int, object]   = {}   # chat_id → sent drop Message object
_expiry_tasks:     dict[int, asyncio.Task] = {} # chat_id → expiry countdown task

_DROP_EXPIRE_SECS   = 180   # 3 minutes
_DROP_MSG_DEFAULT   = 10    # default messages needed to trigger a drop

# ── XP per correct guess (by rarity) ─────────────────────────────────────────
_XP_MAP: dict[str, int] = {
    "⚪ Common":            15,
    "🟣 Rare":              30,
    "🟡 Legendary":         55,
    "🔮 Mythical":         120,
    "💮 Special Edition":  250,
    "🌌 Universal Limited": 1000,
}
_XP_DEFAULT    = 15    # fallback if rarity string unrecognised

# ── Weighted drop rates ───────────────────────────────────────────────────────
# Higher weight = more likely to appear in a drop.
_DROP_WEIGHT: dict[str, int] = {
    "⚪ Common":            80,
    "🟣 Rare":              75,
    "🟡 Legendary":         65,
    "🔮 Mythical":          30,
    "💮 Special Edition":   18,
    "🌌 Universal Limited":  5,
}
_WEIGHT_DEFAULT = 1    # fallback weight for unknown rarity

_DEFAULT_LIMIT = 10    # fallback global limit if character has no limit field


# ── Level helpers (mirror of profile.py — kept local to avoid circular import) ─

def _xp_for_level(level: int) -> int:
    return int(200 * (level ** 1.5))


def _calc_level(xp: int) -> tuple[int, int, int]:
    """Returns (level, xp_into_level, xp_needed_for_next)."""
    level = 1
    while _xp_for_level(level + 1) <= xp:
        level += 1
    floor = _xp_for_level(level)
    nxt   = _xp_for_level(level + 1)
    return level, xp - floor, nxt - floor


# ── Rarity helper ─────────────────────────────────────────────────────────────

def _split_rarity(rarity: str) -> tuple[str, str]:
    parts = rarity.split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "💎", rarity


# ── Drop message-count threshold (per group, stored in DB) ────────────────────

async def _get_drop_threshold(chat_id: int) -> int:
    """Return how many messages trigger a drop for this chat."""
    doc = await user_totals_collection.find_one({"chat_id": chat_id})
    if doc and "drop_msg_count" in doc:
        return max(1, int(doc["drop_msg_count"]))
    return _DROP_MSG_DEFAULT


# ── Rolling window ────────────────────────────────────────────────────────────

def _rolling_window_size(total_chars: int) -> int:
    return max(20, total_chars // 2)


# ── Send drop ─────────────────────────────────────────────────────────────────

async def _send_drop(chat_id: int, bot, forced_char: dict | None = None) -> None:
    """Pick a random (or forced) character and post it to the chat."""
    if forced_char is not None:
        char = forced_char
    else:
        all_chars = await collection.find({}).to_list(length=5000)
        if not all_chars:
            LOGGER.debug("No characters in DB — skipping drop for chat %s", chat_id)
            return

        available = [
            c for c in all_chars
            if c.get("claimed_count", 0) < c.get("limit", _DEFAULT_LIMIT)
        ]
        if not available:
            LOGGER.info("All characters sold out in chat %s — skipping drop", chat_id)
            return

        window = _rolling_window_size(len(available))
        sent   = _sent_ids.get(chat_id, [])
        unsent = [c for c in available if c["id"] not in sent]

        if not unsent:
            _sent_ids[chat_id] = []
            unsent = available
            LOGGER.debug("Sent-IDs window cleared for chat %s", chat_id)

        weights = [_DROP_WEIGHT.get(c.get("rarity", ""), _WEIGHT_DEFAULT) for c in unsent]
        char = random.choices(unsent, weights=weights, k=1)[0]
        new_sent = sent + [char["id"]]
        _sent_ids[chat_id] = new_sent[-window:]

    _active_char[chat_id] = char
    _claimers[chat_id]    = set()

    # ── Resolve img_url (photo only — videos are only used in /check) ────────────
    import io as _io
    from telegram import InputFile as _InputFile
    img_to_send = char["img_url"]
    if isinstance(img_to_send, str) and "api.telegram.org" in img_to_send:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=20) as _http:
                _r = await _http.get(img_to_send)
                _r.raise_for_status()
                img_to_send = _InputFile(_io.BytesIO(_r.content), filename="photo.jpg")
            LOGGER.info("CDN URL recovered for char %s via download", char["id"])
        except Exception as _dl_err:
            LOGGER.warning("CDN URL download failed for %s: %s", char["id"], _dl_err)
            return

    _is_file_upload = not isinstance(img_to_send, str)
    _write_timeout  = 60 if _is_file_upload else 10

    drop_caption = (
        "✨ <b>A new character appeared!</b>\n\n"
        "<i>Use /guess [name] to add them to your harem!</i>"
    )

    try:
        LOGGER.info("Sending PHOTO drop for char %s", char["id"])
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=img_to_send,
            caption=drop_caption,
            parse_mode=ParseMode.HTML,
            write_timeout=_write_timeout,
            read_timeout=30,
        )
        if msg.photo:
            new_fid = msg.photo[-1].file_id
            if new_fid != char.get("img_url"):
                char["img_url"] = new_fid
                await collection.update_one(
                    {"id": char["id"]},
                    {"$set": {"img_url": new_fid}},
                )

        LOGGER.info("Drop sent to chat %s: %s (%s)",
                    chat_id, char["name"], char.get("rarity", "?"))

        # ── Save message & start 5-minute expiry countdown ────────────────────
        _drop_msg[chat_id] = msg
        old_exp = _expiry_tasks.pop(chat_id, None)
        if old_exp and not old_exp.done():
            old_exp.cancel()
        _expiry_tasks[chat_id] = asyncio.create_task(
            _expire_drop(chat_id, char, bot)
        )
        LOGGER.info("Expiry timer started for chat %s (5 min)", chat_id)

    except Exception as e:
        _active_char.pop(chat_id, None)
        LOGGER.warning("Drop failed in chat %s: %s", chat_id, e)


# ── Drop expiry coroutine ──────────────────────────────────────────────────────

async def _expire_drop(chat_id: int, char: dict, bot) -> None:
    """Wait DROP_EXPIRE_SECS then expire the drop if still unclaimed."""
    await asyncio.sleep(_DROP_EXPIRE_SECS)

    # Still the same active char?
    if _active_char.get(chat_id) is not char:
        return  # already claimed / replaced

    _active_char.pop(chat_id, None)
    _claimers.pop(chat_id, None)

    LOGGER.info("Drop expired for chat %s — char %s", chat_id, char.get("id"))
    exp_text = "      ⏰  <b>C A R D   E X P I R E D</b>  ⏰"

    drop_message = _drop_msg.pop(chat_id, None)
    try:
        if drop_message:
            # Edit caption of the original drop message
            await drop_message.edit_caption(
                caption=exp_text,
                parse_mode=ParseMode.HTML,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=exp_text,
                parse_mode=ParseMode.HTML,
            )
    except Exception as err:
        LOGGER.debug("Expiry message failed for chat %s: %s", chat_id, err)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=exp_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


# ── Message handler (count-based drop trigger) ────────────────────────────────

async def message_counter(update: Update, context: CallbackContext) -> None:
    if not update.effective_chat or update.effective_chat.type == "private":
        return
    if not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    _registered_chats.add(chat_id)

    # ── Count messages; drop when threshold reached ────────────────────────────
    _msg_count[chat_id] = _msg_count.get(chat_id, 0) + 1
    threshold = await _get_drop_threshold(chat_id)

    if _msg_count[chat_id] >= threshold:
        _msg_count[chat_id] = 0   # reset counter
        # Only drop if no character is currently active
        if not _active_char.get(chat_id):
            asyncio.create_task(_send_drop(chat_id, context.bot))
            LOGGER.info("Message threshold (%d) reached → drop for chat %s",
                        threshold, chat_id)

    # Anti-spam: warn if user sends ≥15 messages within 10 seconds
    _SPAM_WINDOW  = 10    # seconds
    _SPAM_LIMIT   = 15    # messages in that window
    _WARN_COOLDOWN = 600  # seconds before warning same user again

    now = time.time()
    key = (chat_id, user_id)
    window = _last_user.get(key)

    if window and now - window["start"] <= _SPAM_WINDOW:
        window["count"] += 1
        if window["count"] >= _SPAM_LIMIT:
            warned_at = _warned.get(user_id, 0)
            if now - warned_at >= _WARN_COOLDOWN:
                _warned[user_id] = now
                window["count"] = 0   # reset after warning
                try:
                    await update.message.reply_text(
                        f"⚠️ {escape(update.effective_user.first_name)}, "
                        "message များလွန်းနေတယ်!"
                    )
                except Exception:
                    pass
    else:
        # Window expired or new user — start fresh
        _last_user[key] = {"start": now, "count": 1}


def restart_drop_task(chat_id: int, bot) -> None:
    """Reset the message counter for a group (called after threshold change)."""
    _msg_count[chat_id] = 0
    LOGGER.info("Message counter reset for chat %s", chat_id)


# ── /guess ────────────────────────────────────────────────────────────────────

async def guess(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    u       = update.effective_user

    char = _active_char.get(chat_id)
    if not char:
        return

    claimers = _claimers.setdefault(chat_id, set())

    if len(claimers) >= 1:
        if user_id in claimers:
            await update.message.reply_text(
                "✅ မင်း ဒီ drop ကို ရပြီးပြီ! နောက် drop ကို စောင့်ပေး။"
            )
        else:
            await update.message.reply_text(
                "❌ တစ်ယောက်ပြီးယူသွားပြီ! နောက် drop ကို စောင့်ပေး!"
            )
        return

    if user_id in claimers:
        await update.message.reply_text(
            "✅ မင်း ဒီ drop ကို ရပြီးပြီ! နောက် drop ကို စောင့်ပေး။"
        )
        return

    user_guess = " ".join(context.args).strip().lower() if context.args else ""
    if not user_guess:
        await update.message.reply_text("Usage: /guess <character name>")
        return

    if any(bad in user_guess for bad in ("()", "&&", "||", "<script")):
        await update.message.reply_text("❌ Invalid input.")
        return

    name_parts = char["name"].lower().split()
    correct = (
        sorted(name_parts) == sorted(user_guess.split())
        or any(part == user_guess for part in name_parts)
    )

    if not correct:
        await update.message.reply_text("❌ Wrong name, try again!")
        return

    # ── Correct guess ──────────────────────────────────────────────────────────
    claimers.add(user_id)
    _active_char.pop(chat_id, None)
    _drop_msg.pop(chat_id, None)
    # Cancel the expiry countdown since someone claimed it
    _exp = _expiry_tasks.pop(chat_id, None)
    if _exp and not _exp.done():
        _exp.cancel()

    # Rarity-based XP
    xp_earned = _XP_MAP.get(char.get("rarity", ""), _XP_DEFAULT)

    # Fetch old XP for level-up check (before increment)
    old_doc   = await user_collection.find_one({"id": user_id})
    old_xp    = (old_doc or {}).get("xp", 0)
    old_level = _calc_level(old_xp)[0]

    # Update global claimed count
    char_global_limit = char.get("limit", _DEFAULT_LIMIT)
    char_prev_claimed = char.get("claimed_count", 0)
    char_new_claimed  = char_prev_claimed + 1

    await collection.update_one(
        {"id": char["id"]},
        {"$inc": {"claimed_count": 1}},
    )

    # Update user document
    await user_collection.update_one(
        {"id": user_id},
        {
            "$push": {"characters": char},
            "$inc":  {"total_guesses": 1, "xp": xp_earned},
            "$set":  {"username": u.username, "first_name": u.first_name},
            "$setOnInsert": {"coins": 0, "wins": 0, "favorites": []},
        },
        upsert=True,
    )

    # Group totals
    await group_user_totals_collection.update_one(
        {"user_id": user_id, "group_id": chat_id},
        {"$set": {"username": u.username, "first_name": u.first_name},
         "$inc": {"count": 1}},
        upsert=True,
    )
    await top_global_groups_collection.update_one(
        {"group_id": chat_id},
        {"$set": {"group_name": update.effective_chat.title},
         "$inc": {"count": 1}},
        upsert=True,
    )

    # ── Level-up check ─────────────────────────────────────────────────────────
    new_xp    = old_xp + xp_earned
    new_level = _calc_level(new_xp)[0]

    _LEVEL_UP_COINS = 200
    if new_level > old_level:
        mention  = f'<a href="tg://user?id={user_id}">{escape(u.first_name)}</a>'
        lv_text  = (
            f"🎉 {mention} has reached <b>Level {new_level}</b>! ✨\n"
            f"<i>+{_LEVEL_UP_COINS} 🪙 Bonus coins!</i>"
        )
        # Grant bonus coins
        await user_collection.update_one(
            {"id": user_id},
            {"$inc": {"coins": _LEVEL_UP_COINS}},
        )
        for gid in list(_registered_chats):
            try:
                await context.bot.send_message(
                    gid, lv_text, parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    # ── Success reply ──────────────────────────────────────────────────────────
    rar_emoji, rar_name = _split_rarity(char["rarity"])
    is_sold_out = char_new_claimed >= char_global_limit
    sold_out_line = (
        f"\n🚫 <b>Sold Out! ({char_new_claimed}/{char_global_limit})</b>"
        if is_sold_out else ""
    )

    # Total unique characters owned (after this catch)
    updated_user = await user_collection.find_one({"id": user_id}, {"characters": 1})
    total_owned  = len({c["id"] for c in (updated_user or {}).get("characters", [])})

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🔱 Waifus ({total_owned})",
            switch_inline_query_current_chat=f"harem.{user_id}",
        )],
    ])

    caption = (
        f'🪷 <a href="tg://user?id={user_id}">{escape(u.first_name)}</a>'
        f', ʏᴏᴜ ɢᴏᴛ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ!\n\n'
        f'🫧 Nᴀᴍᴇ: <b>{escape(char["name"])}</b>\n'
        f'{rar_emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {rar_name}\n'
        f'🏖️ Aɴɪᴍᴇ: {escape(char["anime"])} '
        f'(<b>{char_new_claimed}/{char_global_limit}</b>)\n\n'
        f'Added to your harem! +{xp_earned} XP ✨'
        f'{sold_out_line}'
    )

    media_id   = char.get("img_url")
    char_mtype = char.get("media_type", "photo")

    if media_id:
        if char_mtype == "video":
            await update.message.reply_video(
                video=media_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            await update.message.reply_photo(
                photo=media_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
    else:
        await update.message.reply_text(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )


# ── /fav ──────────────────────────────────────────────────────────────────────

async def fav(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /fav <character_id>")
        return

    char_id  = context.args[0]
    user_doc = await user_collection.find_one({"id": user_id})
    if not user_doc:
        await update.message.reply_text("You haven't guessed any characters yet.")
        return

    char = next((c for c in user_doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await update.message.reply_text("That character isn't in your collection.")
        return

    await user_collection.update_one({"id": user_id}, {"$set": {"favorites": [char_id]}})
    await update.message.reply_text(
        f"⭐ <b>{escape(char['name'])}</b> set as your favourite!",
        parse_mode=ParseMode.HTML,
    )


# ── /forcedrop ────────────────────────────────────────────────────────────────

async def forcedrop(update: Update, context: CallbackContext) -> None:
    """Owner only.
    /forcedrop             → random drop in current group
    /forcedrop <char_id>   → drop that specific character in current group
    """
    caller_id = update.effective_user.id
    if caller_id != OWNER_ID:
        await update.message.reply_text("❌ Owner only.")
        return

    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("❌ Group ထဲမှာ run ပါ။")
        return

    chat_id = chat.id
    _registered_chats.add(chat_id)

    # ── Specific character drop: /forcedrop <char_id> ─────────────────────────
    if context.args:
        char_id = context.args[0].strip()
        char = await collection.find_one({"id": char_id})
        if not char:
            await update.message.reply_text(
                f"❌ Character ID <code>{escape(char_id)}</code> မတွေ့ဘူး။",
                parse_mode=ParseMode.HTML,
            )
            return

        rar_emoji, rar_name = _split_rarity(char.get("rarity", ""))
        claimed = char.get("claimed_count", 0)
        limit   = char.get("limit", _DEFAULT_LIMIT)
        if claimed >= limit:
            await update.message.reply_text(
                f"❌ <b>{escape(char['name'])}</b> ကုန်သွားပြီ! ({claimed}/{limit})",
                parse_mode=ParseMode.HTML,
            )
            return

        await update.message.reply_text(
            f"🎴 <b>{escape(char['name'])}</b> ({rar_emoji} {rar_name}) drop ချနေပြီ…",
            parse_mode=ParseMode.HTML,
        )
        await _send_drop(chat_id, context.bot, forced_char=char)
        return

    # ── Random drop ───────────────────────────────────────────────────────────
    await update.message.reply_text("🎴 Forcing a character drop...")
    await _send_drop(chat_id, context.bot)


# ── /setdropannounce ──────────────────────────────────────────────────────────

async def _setannounce_start(update: Update, context: CallbackContext) -> int:
    """Owner-only: begin setting a new pre-drop announcement."""
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ ဒီ command ကို bot DM မှာသာ သုံးပါ။")
        return ConversationHandler.END

    doc = await bot_settings_collection.find_one({"key": "drop_announce"})
    current = ""
    if doc:
        t = doc.get("type","text")
        v = doc.get("value","")
        current = f"\n\n<b>လက်ရှိ:</b> [{t}] <code>{escape(str(v))}</code>"

    await update.message.reply_text(
        "🎴 <b>Pre-Drop Announcement</b>\n\n"
        "Drop မကျခင် <b>30 စက္ကန့်</b>အလိုမှာ group တွေကို ကြေငြာချင်တဲ့\n"
        "<b>sticker / emoji / text</b> ကို ယခု ပို့ပါ။\n\n"
        "❌ ဖျက်ချင်ရင် /cleardropannounce"
        f"{current}",
        parse_mode=ParseMode.HTML,
    )
    return _WAIT_ANNOUNCE


async def _setannounce_receive(update: Update, context: CallbackContext) -> int:
    """Receive sticker or text/emoji and save as the announcement."""
    msg = update.message

    if msg.sticker:
        fid = msg.sticker.file_id
        await bot_settings_collection.update_one(
            {"key": "drop_announce"},
            {"$set": {"key": "drop_announce", "type": "sticker", "value": fid}},
            upsert=True,
        )
        await msg.reply_text(
            "✅ <b>Pre-Drop Sticker သတ်မှတ်ပြီး!</b>\n"
            "Drop မကျခင် 30 sec အလိုမှာ group တွေကို ဒီ sticker ပို့မယ်။",
            parse_mode=ParseMode.HTML,
        )
    elif msg.text:
        text = msg.text.strip()
        await bot_settings_collection.update_one(
            {"key": "drop_announce"},
            {"$set": {"key": "drop_announce", "type": "text", "value": text}},
            upsert=True,
        )
        await msg.reply_text(
            f"✅ <b>Pre-Drop Announcement သတ်မှတ်ပြီး!</b>\n\n"
            f"Preview:\n{text}",
            parse_mode=ParseMode.HTML,
        )
    else:
        await msg.reply_text("❌ Text သို့ Sticker သာ လက်ခံနိုင်သည်။ ထပ်မံ ပို့ပါ သို့ /cancel")
        return _WAIT_ANNOUNCE

    return ConversationHandler.END


async def _setannounce_cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("❌ ဖျက်လိုက်ပြီ။")
    return ConversationHandler.END


async def cleardropannounce(update: Update, context: CallbackContext) -> None:
    """Owner-only: remove the pre-drop announcement."""
    if update.effective_user.id != OWNER_ID:
        return
    await bot_settings_collection.delete_one({"key": "drop_announce"})
    await update.message.reply_text("✅ Pre-Drop Announcement ဖျက်ပြီးပြီ။")


# ── Register handlers ─────────────────────────────────────────────────────────

_announce_conv = ConversationHandler(
    entry_points=[CommandHandler("setdropannounce", _setannounce_start)],
    states={
        _WAIT_ANNOUNCE: [
            MessageHandler(
                filters.ChatType.PRIVATE & (filters.TEXT | filters.Sticker.ALL),
                _setannounce_receive,
            ),
        ],
    },
    fallbacks=[CommandHandler("cancel", _setannounce_cancel)],
    per_message=False,
)

application.add_handler(CommandHandler(
    ["guess", "protecc", "collect", "grab", "hunt"], guess, block=False
))
application.add_handler(CommandHandler("fav",            fav,              block=False))
application.add_handler(CommandHandler("forcedrop",      forcedrop,        block=False))
application.add_handler(CommandHandler("cleardropannounce", cleardropannounce, block=False))
application.add_handler(_announce_conv)
application.add_handler(MessageHandler(
    filters.ChatType.GROUPS & ~filters.COMMAND,
    message_counter,
    block=False,
))
