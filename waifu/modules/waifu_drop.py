"""
modules/waifu_drop.py

Core game loop:
  - Message counter → threshold drop
  - APScheduler timed drop every N minutes
  - /guess to claim
  - /fav to favourite
  - Anti-spam (10 consecutive messages from same user → 10-min ignore)

Bug fixes vs previous version:
  1. _active_char is cleared immediately after a correct guess so the same
     drop cannot be guessed twice. Previously it lingered until the NEXT drop.
  2. _sent_ids now uses a rolling window (capped at half the catalogue size,
     minimum 20). The old "len(sent)==len(all_chars)" reset condition was
     never triggered when new characters were added to the DB while the bot
     was running, permanently blacklisting those characters from reappearing.
  3. XP is now awarded for a correct guess (50 XP, configurable).
"""
import asyncio
import random
import time
from html import escape

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
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
_active_char:      dict[int, dict]      = {}  # chat_id → active character
_claimers:         dict[int, set]       = {}  # chat_id → set of user_ids who claimed
_msg_counts:       dict[int, int]       = {}  # chat_id → message counter
_last_user:        dict[int, dict]      = {}  # chat_id → {user_id, count}
_warned:           dict[int, float]     = {}  # user_id → timestamp of last warning
_sent_ids:         dict[int, list]      = {}  # rolling window of sent char IDs
_registered_chats: set[int]            = set()

scheduler = AsyncIOScheduler(timezone="UTC")

# XP reward. Per-drop: 1 person can claim (global limit per character is set in DB)
_XP_PER_GUESS  = 50
_DEFAULT_LIMIT = 10   # fallback global limit if character has no limit field


# ── Rarity helper ─────────────────────────────────────────────────────────────

def _split_rarity(rarity: str) -> tuple[str, str]:
    """Return (emoji, name) from stored rarity string like '🟣 Rare'."""
    parts = rarity.split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "💎", rarity


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _chat_frequency(chat_id: int) -> int:
    doc = await user_totals_collection.find_one({"chat_id": chat_id})
    return int(doc["message_frequency"]) if doc and "message_frequency" in doc \
        else Config.DEFAULT_MSG_FREQUENCY


def _rolling_window_size(total_chars: int) -> int:
    """
    How many recently-sent IDs to remember before a character can reappear.
    Capped at half the catalogue (minimum 20) so even a 1-character DB works.
    """
    return max(20, total_chars // 2)


async def _send_drop(chat_id: int, bot) -> None:
    """Pick a random unseen-recently character and post it to the chat."""
    all_chars = await collection.find({}).to_list(length=5000)
    if not all_chars:
        LOGGER.debug("No characters in DB — skipping drop for chat %s", chat_id)
        return

    # Filter out sold-out characters (global claimed_count >= limit)
    available = [
        c for c in all_chars
        if c.get("claimed_count", 0) < c.get("limit", _DEFAULT_LIMIT)
    ]
    if not available:
        LOGGER.info("All characters sold out in chat %s — skipping drop", chat_id)
        return

    window = _rolling_window_size(len(available))
    sent   = _sent_ids.get(chat_id, [])

    # Characters not in the rolling window
    unsent = [c for c in available if c["id"] not in sent]

    # If every character has been seen recently, clear the window and start fresh
    if not unsent:
        _sent_ids[chat_id] = []
        unsent = available
        LOGGER.debug("Sent-IDs window cleared for chat %s (all %d chars seen)",
                     chat_id, len(available))

    char = random.choice(unsent)

    # Append to rolling window; trim to keep only the most recent `window` entries
    new_sent = sent + [char["id"]]
    _sent_ids[chat_id] = new_sent[-window:]

    # Register as the active drop — reset claim state
    _active_char[chat_id] = char
    _claimers[chat_id]    = set()   # fresh drop, anyone can claim

    try:
        await bot.send_photo(
            chat_id=chat_id,
            photo=char["img_url"],
            caption=(
                f"✨ <b>A new character appeared!</b>\n\n"
                f"<i>Use /guess [name] to add them to your harem!</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
        LOGGER.info("Drop sent to chat %s: %s (%s)",
                    chat_id, char["name"], char.get("rarity", "?"))
    except Exception as e:
        # Roll back state if we couldn't actually post the message
        _active_char.pop(chat_id, None)
        LOGGER.warning("Drop failed in chat %s: %s", chat_id, e)


# ── Scheduler ─────────────────────────────────────────────────────────────────

async def _timed_drop_job(bot) -> None:
    for chat_id in list(_registered_chats):
        await _send_drop(chat_id, bot)


def start_scheduler(bot) -> None:
    scheduler.add_job(
        _timed_drop_job,
        trigger=IntervalTrigger(minutes=Config.DROP_INTERVAL_MIN),
        kwargs={"bot": bot},
        id="timed_drop",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()
    LOGGER.info("Drop scheduler started — interval: every %d min",
                Config.DROP_INTERVAL_MIN)


# ── Message counter ───────────────────────────────────────────────────────────

async def message_counter(update: Update, context: CallbackContext) -> None:
    if not update.effective_chat or update.effective_chat.type == "private":
        return
    if not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    _registered_chats.add(chat_id)

    # ── Always increment counter first (anti-spam only affects warnings) ──────
    _msg_counts[chat_id] = _msg_counts.get(chat_id, 0) + 1

    # ── Anti-spam tracking (warn only — never blocks the counter) ─────────────
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
                        f"consecutive messages များလွန်းတယ်!"
                    )
                except Exception:
                    pass
    else:
        _last_user[chat_id] = {"user_id": user_id, "count": 1}

    # ── Check threshold and drop ───────────────────────────────────────────────
    freq = await _chat_frequency(chat_id)
    if _msg_counts[chat_id] >= freq:
        _msg_counts[chat_id] = 0
        await _send_drop(chat_id, context.bot)


# ── /guess ────────────────────────────────────────────────────────────────────

async def guess(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    u       = update.effective_user

    # No active drop in this chat
    char = _active_char.get(chat_id)
    if not char:
        return   # silent — no character is waiting

    claimers = _claimers.setdefault(chat_id, set())

    # Per-drop: only 1 person can claim each drop session
    if len(claimers) >= 1:
        already_claimed = update.effective_user.id in claimers
        if already_claimed:
            await update.message.reply_text(
                "✅ မင်း ဒီ drop ကို ရပြီးပြီ! နောက် drop ကို စောင့်ပေး။"
            )
        else:
            await update.message.reply_text(
                "❌ တစ်ယောက်ပြီးယူသွားပြီ! နောက် drop ကို စောင့်ပေး!"
            )
        return

    # This user already claimed this drop (safety check)
    if user_id in claimers:
        await update.message.reply_text(
            "✅ မင်း ဒီ drop ကို ရပြီးပြီ! နောက် drop ကို စောင့်ပေး။"
        )
        return

    user_guess = " ".join(context.args).strip().lower() if context.args else ""
    if not user_guess:
        await update.message.reply_text("Usage: /guess <character name>")
        return

    # Reject malicious input
    if any(bad in user_guess for bad in ("()", "&&", "||", "<script")):
        await update.message.reply_text("❌ Invalid input.")
        return

    # Name matching: full name OR any single word
    name_parts = char["name"].lower().split()
    correct = (
        sorted(name_parts) == sorted(user_guess.split())
        or any(part == user_guess for part in name_parts)
    )

    if not correct:
        await update.message.reply_text("❌ Wrong name, try again!")
        return

    # ── Correct guess ─────────────────────────────────────────────────────────
    claimers.add(user_id)

    # Clear active drop (only 1 claimer per session)
    _active_char.pop(chat_id, None)

    # ── Increment global claimed_count in collection DB ────────────────────────
    char_global_limit   = char.get("limit", _DEFAULT_LIMIT)
    char_prev_claimed   = char.get("claimed_count", 0)
    char_new_claimed    = char_prev_claimed + 1

    await collection.update_one(
        {"id": char["id"]},
        {"$inc": {"claimed_count": 1}},
    )

    # ── Persist to user document ───────────────────────────────────────────────
    await user_collection.update_one(
        {"id": user_id},
        {
            "$push": {"characters": char},
            "$inc":  {"total_guesses": 1, "xp": _XP_PER_GUESS},
            "$set":  {"username": u.username, "first_name": u.first_name},
            "$setOnInsert": {"coins": 0, "wins": 0, "favorites": []},
        },
        upsert=True,
    )

    # ── Group totals ───────────────────────────────────────────────────────────
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

    # ── Success reply ─────────────────────────────────────────────────────────
    rar_emoji, rar_name = _split_rarity(char["rarity"])
    is_sold_out = char_new_claimed >= char_global_limit
    sold_out_line = (
        f"\n🚫 <b>Sold Out! ({char_new_claimed}/{char_global_limit})</b>"
        if is_sold_out else ""
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📖 My Harem", callback_data=f"act:harem"),
        InlineKeyboardButton("🔍 via Inline", switch_inline_query_current_chat=f"collection.{user_id}"),
    ]])

    caption = (
        f'🪷 <a href="tg://user?id={user_id}">{escape(u.first_name)}</a>'
        f', ʏᴏᴜ ɢᴏᴛ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ!\n\n'
        f'🫧 Nᴀᴍᴇ: <b>{escape(char["name"])}</b>\n'
        f'{rar_emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {rar_name}\n'
        f'🏖️ Aɴɪᴍᴇ: {escape(char["anime"])} '
        f'(<b>{char_new_claimed}/{char_global_limit}</b>)\n\n'
        f'Added to your harem! +{_XP_PER_GUESS} XP ✨'
        f'{sold_out_line}'
    )

    # Send as photo so the character image is visible immediately
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
        await update.message.reply_text(
            "❌ Run this in a group to trigger a drop there.")
        return

    chat_id = chat.id
    _registered_chats.add(chat_id)
    await update.message.reply_text("🎴 Forcing a character drop...")
    await _send_drop(chat_id, context.bot)


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler(
    ["guess", "protecc", "collect", "grab", "hunt"], guess, block=False
))
application.add_handler(CommandHandler("fav", fav, block=False))
application.add_handler(CommandHandler("forcedrop", forcedrop, block=False))
application.add_handler(MessageHandler(
    filters.ChatType.GROUPS & ~filters.COMMAND,
    message_counter,
    block=False,
))