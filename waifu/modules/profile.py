"""
modules/profile.py — /profile command showing full user stats.
Profile image = last caught character (falls back to favourite, then random).
"""
import math
import random
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection, PHOTO_URL


# ── helpers ──────────────────────────────────────────────────────────────────

def _xp_for_level(level: int) -> int:
    return int(200 * (level ** 1.5))


def _calc_level(xp: int) -> tuple[int, int, int]:
    level = 1
    while _xp_for_level(level + 1) <= xp:
        level += 1
    floor = _xp_for_level(level)
    nxt   = _xp_for_level(level + 1)
    return level, xp - floor, nxt - floor


def _bar(value: int, maximum: int, length: int = 10) -> str:
    filled = int(length * value / max(maximum, 1))
    return "▓" * filled + "░" * (length - filled)


def _total_waifu_count() -> int:
    """Approximate total unique waifus — used for harem percentage denominator."""
    return 5798   # update this if character DB grows significantly


# ── command ───────────────────────────────────────────────────────────────────

async def profile(update: Update, context: CallbackContext) -> None:
    # Support /profile, reply, or /profile @username
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
    elif context.args:
        username = context.args[0].lstrip("@")
        u_doc = await user_collection.find_one({"username": username})
        if not u_doc:
            await update.message.reply_text("❌ User not found.")
            return
        target = None
    else:
        target = update.effective_user

    if target:
        u_doc = await user_collection.find_one({"id": target.id})

    if not u_doc:
        await update.message.reply_text("❌ That user hasn't played yet.")
        return

    uid        = u_doc["id"]
    first_name = escape(u_doc.get("first_name", "User"))
    username   = u_doc.get("username", "")
    coins      = u_doc.get("coins", 0)
    chars      = u_doc.get("characters", [])
    wins       = u_doc.get("wins", 0)
    guesses    = u_doc.get("total_guesses", 0)
    xp         = u_doc.get("xp", 0)

    unique_count = len({c["id"] for c in chars})
    total_count  = len(chars)
    total_waifus = _total_waifu_count()
    harem_pct    = (unique_count / total_waifus * 100) if total_waifus else 0

    level, xp_in, xp_need = _calc_level(xp)
    bar = _bar(xp_in, xp_need, 10)

    tag = f"@{username}" if username else f"#{uid}"

    # ── Box-style caption like screenshot ────────────────────────────────────
    caption = (
        f"┌ 🎀 <b>CATCHER PROFILE</b> 🎀 ┐\n"
        f"├➤ 👤 <b>USER:</b> {first_name}\n"
        f"├➤ 🔌 <b>USER ID:</b> {uid}\n"
        f"├➤ ⚡ <b>TOTAL CHARACTER:</b> {unique_count} ({total_count})\n"
        f"├➤ ⭕ <b>HAREM:</b> {unique_count}/{total_waifus} ({harem_pct:.3f}%)\n"
        f"├➤ 💰 <b>COINS:</b> {coins:,}\n"
        f"├➤ ⚔️ <b>DUEL WINS:</b> {wins}\n"
        f"├➤ 🎯 <b>GUESSES:</b> {guesses}\n"
        f"├➤ ℹ️ <b>EXPERIENCE LEVEL:</b> {level}\n"
        f"├➤ 📝 <b>PROGRESS BAR:</b>\n"
        f"└  {bar}  ({xp_in:,}/{xp_need:,} XP)"
    )

    # ── Profile image: last caught → favourite → random ───────────────────────
    photo: str | None = None

    # Last caught character's image
    if chars:
        last_char = chars[-1]
        photo     = last_char.get("img_url")

    # Fallback: first favourite
    if not photo:
        fav_id = (u_doc.get("favorites") or [None])[0]
        if fav_id:
            fav_char = next((c for c in chars if c["id"] == fav_id), None)
            photo    = (fav_char or {}).get("img_url")

    # Fallback: random bot background
    if not photo and PHOTO_URL:
        photo = random.choice(PHOTO_URL)

    if photo:
        try:
            await update.message.reply_photo(
                photo, caption=caption, parse_mode=ParseMode.HTML
            )
            return
        except Exception:
            pass

    await update.message.reply_text(caption, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("profile", profile, block=False))
