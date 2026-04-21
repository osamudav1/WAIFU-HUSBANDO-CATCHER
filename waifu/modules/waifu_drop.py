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
from datetime import datetime, timezone
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import KeyboardButtonStyle, ParseMode
from telegram.ext import (
    CallbackContext, CommandHandler, ConversationHandler,
    MessageHandler, filters,
)

from waifu import (
    application, collection, group_user_totals_collection,
    top_global_groups_collection, user_collection, user_totals_collection,
    bot_settings_collection, registered_chats,
    LOGGER, OWNER_ID, sudo_users,
)
from waifu.cache import (
    db_op, user_lock,
    get_user, set_user, invalidate_user,
    get_char_list, set_char_list,
    get_chat_cfg, set_chat_cfg,
)
# ── Conversation state ────────────────────────────────────────────────────────
_WAIT_ANNOUNCE = 0

# ── Per-chat in-memory state ──────────────────────────────────────────────────
_active_char:      dict[int, dict]      = {}   # chat_id → active character
_claimers:         dict[int, set]       = {}   # chat_id → set of user_ids who claimed
_last_user:        dict[int, dict]      = {}   # chat_id → {user_id, count}
_warned:           dict[int, float]     = {}   # user_id → timestamp of last warning
_sent_ids:         dict[int, list]      = {}   # rolling window of sent char IDs
_msg_count:        dict[int, int]      = {}   # chat_id → messages since last drop
_drop_msg:         dict[int, object]   = {}   # chat_id → sent drop Message object
_expiry_tasks:     dict[int, asyncio.Task] = {} # chat_id → expiry countdown task

_DROP_EXPIRE_SECS   = 120   # 2 minutes
_DROP_MSG_DEFAULT   = 10    # default messages needed to trigger a drop

# ── XP per correct guess (by rarity) ─────────────────────────────────────────
_XP_MAP: dict[str, int] = {
    "⚪ Common":            15,
    "🟣 Rare":              30,
    "🟤 Medium":            42,
    "🟡 Legendary":         55,
    "🔮 Mythical":         120,
    "🪞 Supreme":          180,
    "💮 Special Edition":  250,
    "🌐 Global":           400,
    "✖️ CrossVerse":        600,
    "🌌 Universal":        1000,
}
_XP_DEFAULT    = 15    # fallback if rarity string unrecognised

# ── Weighted drop rates ───────────────────────────────────────────────────────
# Higher weight = more likely to appear in a drop.
_DROP_WEIGHT: dict[str, int] = {
    "⚪ Common":            80,
    "🟣 Rare":              75,
    "🟤 Medium":            70,
    "🟡 Legendary":         65,
    "🔮 Mythical":          30,
    "🪞 Supreme":           22,
    "💮 Special Edition":   18,
    "🌐 Global":            10,
    "✖️ CrossVerse":         7,
    "🌌 Universal":           5,
}
_WEIGHT_DEFAULT = 1    # fallback weight for unknown rarity

# ── Premium rarity rules ──────────────────────────────────────────────────────
_PREMIUM_RARITIES = {"🌐 Global", "✖️ CrossVerse", "💮 Special Edition", "🌌 Universal", "🪞 Supreme"}


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
    """Return how many messages trigger a drop for this chat (cached 5 min)."""
    cached = get_chat_cfg(chat_id)
    if cached is not None:
        return cached
    async with db_op():
        doc = await user_totals_collection.find_one({"chat_id": chat_id})
    threshold = max(1, int(doc["drop_msg_count"])) if (doc and "drop_msg_count" in doc) else _DROP_MSG_DEFAULT
    set_chat_cfg(chat_id, threshold)
    return threshold


# ── Rolling window ────────────────────────────────────────────────────────────

def _rolling_window_size(total_chars: int) -> int:
    return max(20, total_chars // 2)


# ── Send drop ─────────────────────────────────────────────────────────────────

async def _send_drop(chat_id: int, bot, forced_char: dict | None = None) -> None:
    """Pick a random (or forced) character and post it to the chat."""
    if forced_char is not None:
        char = forced_char
    else:
        all_chars = get_char_list()
        if all_chars is None:
            async with db_op():
                all_chars = await collection.find({}).to_list(length=5000)
            set_char_list(all_chars)
            LOGGER.debug("Char list cache MISS — fetched %d chars from DB", len(all_chars))
        else:
            LOGGER.debug("Char list cache HIT — %d chars", len(all_chars))
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

    # ── 5-second hint before drop ─────────────────────────────────────────────
    hint_msg = None
    try:
        hint_msg = await bot.send_message(chat_id=chat_id, text="🪄")
    except Exception:
        pass
    await asyncio.sleep(5)

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
        # Delete hint message now that the card is sent
        if hint_msg:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=hint_msg.message_id)
            except Exception:
                pass

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

    registered_chats.add(chat_id)

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

    # ── Per-user lock: prevent concurrent guesses from same user ─────────────────
    async with user_lock(user_id):

    # ── Daily catch limit (25/day) ────────────────────────────────────────────
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lim_doc   = get_user(user_id)
        if lim_doc is None:
            async with db_op():
                lim_doc = await user_collection.find_one({"id": user_id})
            if lim_doc:
                set_user(user_id, lim_doc)
        last_date    = (lim_doc or {}).get("daily_catch_date", "")
        daily_count  = (lim_doc or {}).get("daily_catch_count", 0) if last_date == today_str else 0

        if daily_count >= 25:
            await update.message.reply_text(
                "🎃 ʏᴏᴜʀ ᴄᴀᴛᴄʜ ʟɪᴍɪᴛ ɪs ʀᴇᴀᴄʜᴇᴅ.\n"
                "ʏᴏᴜ ᴄᴀɴ ᴏɴʟʏ ᴄᴀᴛᴄʜ 25 ᴄʜᴀʀᴀᴄᴛᴇʀs ᴘᴇʀ ᴅᴀʏ.\n"
                "ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ᴜɴᴛɪʟ ᴛʜᴇ ɴᴇᴡ ᴅᴀʏ."
            )
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

        # Old XP from cached user doc (no extra DB round-trip)
        old_xp    = (lim_doc or {}).get("xp", 0)
        old_level = _calc_level(old_xp)[0]

    char_rarity = char.get("rarity", "")

    # Update global claimed count
    char_global_limit = char.get("limit", _DEFAULT_LIMIT)
    char_prev_claimed = char.get("claimed_count", 0)
    char_new_claimed  = char_prev_claimed + 1

    await collection.update_one(
        {"id": char["id"]},
        {"$inc": {"claimed_count": 1}},
    )

    # ── Global rarity: assign sequential rank number ───────────────────────────
    global_rank_str = None
    char_to_push    = dict(char)   # copy so we don't mutate the shared dict
    if char.get("rarity") == "🌐 Global":
        global_rank_num  = char_new_claimed          # 1st catcher → 1, 2nd → 2 …
        global_rank_str  = f"{global_rank_num:03d}"  # zero-padded: "001"
        char_to_push["global_rank"] = global_rank_str

    # Update user document
    inc_fields: dict = {"total_guesses": 1, "xp": xp_earned}

    # Daily catch counter — reset if new day, else increment
    if last_date == today_str:
        inc_fields["daily_catch_count"] = 1
        set_fields = {"username": u.username, "first_name": u.first_name}
    else:
        set_fields = {
            "username": u.username, "first_name": u.first_name,
            "daily_catch_date": today_str, "daily_catch_count": 1,
        }

    async with db_op():
        await user_collection.update_one(
            {"id": user_id},
            {
                "$push": {"characters": char_to_push},
                "$inc":  inc_fields,
                "$set":  set_fields,
                "$setOnInsert": {"coins": 0, "wins": 0, "favorites": []},
            },
            upsert=True,
        )
    invalidate_user(user_id)   # cache ကို ပြန် fresh လုပ်

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
        async with db_op():
            await user_collection.update_one(
                {"id": user_id},
                {"$inc": {"coins": _LEVEL_UP_COINS}},
            )
        invalidate_user(user_id)
        group_docs = await top_global_groups_collection.find(
            {}, {"group_id": 1}
        ).to_list(length=500)
        for doc in group_docs:
            gid = doc.get("group_id")
            if not gid:
                continue
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

    # Global rank line (only for 🌐 Global rarity)
    global_rank_line = (
        f"🌐 <b>Global Ranking: <code>#{global_rank_str}</code></b>\n"
        if global_rank_str else ""
    )

    # Total unique characters owned (after this catch) — fresh from DB
    async with db_op():
        updated_user = await user_collection.find_one({"id": user_id}, {"characters": 1})
    set_user(user_id, updated_user or {})
    total_owned  = len({c["id"] for c in (updated_user or {}).get("characters", [])})

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🔱 Waifus ({total_owned})",
            switch_inline_query_current_chat=f"harem.{user_id}",
            style=KeyboardButtonStyle.PRIMARY,
        )],
    ])

    caption = (
        f'🪷 <a href="tg://user?id={user_id}">{escape(u.first_name)}</a>'
        f', ʏᴏᴜ ɢᴏᴛ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ!\n\n'
        f'🫧 Nᴀᴍᴇ: <b>{escape(char["name"])}</b>\n'
        f'{rar_emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {rar_name}\n'
        f'{global_rank_line}'
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
    registered_chats.add(chat_id)

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
application.add_handler(CommandHandler("forcedrop",      forcedrop,        block=False))
application.add_handler(CommandHandler("cleardropannounce", cleardropannounce, block=False))
application.add_handler(_announce_conv)
application.add_handler(MessageHandler(
    filters.ChatType.GROUPS & ~filters.COMMAND,
    message_counter,
    block=False,
))
