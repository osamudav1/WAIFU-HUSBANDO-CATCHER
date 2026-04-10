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
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import (
    application, collection, group_user_totals_collection,
    top_global_groups_collection, user_collection, user_totals_collection,
    LOGGER, OWNER_ID, sudo_users,
)
from waifu.config import Config

# ── Per-chat in-memory state ──────────────────────────────────────────────────
_active_char:      dict[int, dict]      = {}   # chat_id → active character
_claimers:         dict[int, set]       = {}   # chat_id → set of user_ids who claimed
_last_user:        dict[int, dict]      = {}   # chat_id → {user_id, count}
_warned:           dict[int, float]     = {}   # user_id → timestamp of last warning
_sent_ids:         dict[int, list]      = {}   # rolling window of sent char IDs
_registered_chats: set[int]            = set() # all groups ever seen
_drop_tasks:       dict[int, asyncio.Task] = {} # chat_id → asyncio drop task

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


# ── Drop interval ─────────────────────────────────────────────────────────────

async def _chat_drop_interval(chat_id: int) -> int:
    """Return drop interval in minutes for this chat."""
    doc = await user_totals_collection.find_one({"chat_id": chat_id})
    if doc and "drop_interval_minutes" in doc:
        return max(1, int(doc["drop_interval_minutes"]))
    return Config.DROP_INTERVAL_MIN


# ── Rolling window ────────────────────────────────────────────────────────────

def _rolling_window_size(total_chars: int) -> int:
    return max(20, total_chars // 2)


# ── Send drop ─────────────────────────────────────────────────────────────────

async def _send_drop(chat_id: int, bot) -> None:
    """Pick a random unseen-recently character and post it to the chat."""
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

    # Weighted selection by rarity drop rate
    weights = [_DROP_WEIGHT.get(c.get("rarity", ""), _WEIGHT_DEFAULT) for c in unsent]
    char = random.choices(unsent, weights=weights, k=1)[0]
    new_sent = sent + [char["id"]]
    _sent_ids[chat_id] = new_sent[-window:]

    _active_char[chat_id] = char
    _claimers[chat_id]    = set()

    # ── Resolve img_url: recover broken api.telegram.org URLs on-the-fly ─────────
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
            return                          # skip this drop cycle

    # InputFile (bytes) needs a longer write timeout for upload
    _is_file_upload = not isinstance(img_to_send, str)
    _write_timeout  = 60 if _is_file_upload else 10

    try:
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=img_to_send,
            caption=(
                "✨ <b>A new character appeared!</b>\n\n"
                "<i>Use /guess [name] to add them to your harem!</i>"
            ),
            parse_mode=ParseMode.HTML,
            write_timeout=_write_timeout,
            read_timeout=30,
        )
        LOGGER.info("Drop sent to chat %s: %s (%s)",
                    chat_id, char["name"], char.get("rarity", "?"))

        # ── Save THIS bot's file_id so inline CachedPhoto always works ──────────
        # file_ids never expire; CDN URLs do (≈1 hr) — always store file_id.
        if msg.photo:
            new_fid = msg.photo[-1].file_id
            if new_fid != char.get("img_url"):
                char["img_url"] = new_fid
                await collection.update_one(
                    {"id": char["id"]},
                    {"$set": {"img_url": new_fid}},
                )

    except Exception as e:
        _active_char.pop(chat_id, None)
        LOGGER.warning("Drop failed in chat %s: %s", chat_id, e)


# ── Timer loop per group ──────────────────────────────────────────────────────

async def _drop_loop(chat_id: int, bot) -> None:
    """Runs forever: sleep interval minutes → drop → repeat."""
    try:
        while True:
            interval = await _chat_drop_interval(chat_id)
            LOGGER.debug("Chat %s next drop in %d min", chat_id, interval)
            await asyncio.sleep(interval * 60)
            await _send_drop(chat_id, bot)
    except asyncio.CancelledError:
        LOGGER.debug("Drop loop cancelled for chat %s", chat_id)
    except Exception as e:
        LOGGER.error("Drop loop error in chat %s: %s", chat_id, e)


def _start_drop_task(chat_id: int, bot) -> None:
    """Start (or restart) the drop timer for a group."""
    existing = _drop_tasks.get(chat_id)
    if existing and not existing.done():
        return   # already running — don't restart mid-cycle
    task = asyncio.create_task(_drop_loop(chat_id, bot))
    _drop_tasks[chat_id] = task
    LOGGER.info("Drop timer started for chat %s", chat_id)


# ── Message handler (anti-spam + timer registration) ─────────────────────────

async def message_counter(update: Update, context: CallbackContext) -> None:
    if not update.effective_chat or update.effective_chat.type == "private":
        return
    if not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Register chat and start drop timer on first message
    if chat_id not in _registered_chats:
        _registered_chats.add(chat_id)
        _start_drop_task(chat_id, context.bot)

    # Anti-spam tracking (warn only)
    last = _last_user.get(chat_id)
    if last and last["user_id"] == user_id:
        last["count"] += 1
        if last["count"] >= 10:
            warned_at = _warned.get(user_id, 0)
            if time.time() - warned_at >= 600:
                _warned[user_id] = time.time()
                try:
                    await update.message.reply_text(
                        f"⚠️ {escape(update.effective_user.first_name)}, "
                        "consecutive messages များလွန်းတယ်!"
                    )
                except Exception:
                    pass
    else:
        _last_user[chat_id] = {"user_id": user_id, "count": 1}


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

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 My Harem", callback_data="act:harem")],
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

    photo_id = char.get("img_url")
    if photo_id:
        await update.message.reply_photo(
            photo=photo_id,
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
    """Owner/sudo only — instantly trigger a drop in the current group."""
    user_id = update.effective_user.id
    if user_id not in sudo_users and user_id != OWNER_ID:
        await update.message.reply_text("❌ Owner/Sudo only.")
        return

    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("❌ Run this in a group to trigger a drop there.")
        return

    chat_id = chat.id
    if chat_id not in _registered_chats:
        _registered_chats.add(chat_id)
        _start_drop_task(chat_id, context.bot)

    await update.message.reply_text("🎴 Forcing a character drop...")
    await _send_drop(chat_id, context.bot)


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler(
    ["guess", "protecc", "collect", "grab", "hunt"], guess, block=False
))
application.add_handler(CommandHandler("fav",       fav,       block=False))
application.add_handler(CommandHandler("forcedrop", forcedrop, block=False))
application.add_handler(MessageHandler(
    filters.ChatType.GROUPS & ~filters.COMMAND,
    message_counter,
    block=False,
))
