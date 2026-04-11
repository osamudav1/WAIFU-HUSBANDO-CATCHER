"""
modules/check.py — /check <char_id>
Shows character info + global catch count + top 10 catchers.
"""
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, user_collection, LOGGER


# ── helpers ──────────────────────────────────────────────────────────────────

def _rarity_display(rarity: str) -> str:
    """Return 'emoji RARITY: Name' string."""
    parts = rarity.split(" ", 1)
    if len(parts) == 2:
        return f"{parts[0]} RARITY: {parts[1]}"
    return f"🌟 RARITY: {rarity}"


async def _top_catchers(char_id: str, limit: int = 10) -> list[dict]:
    """Aggregate top catchers for a given character ID from users collection."""
    pipeline = [
        {"$unwind": "$characters"},
        {"$match": {"characters.id": char_id}},
        {
            "$group": {
                "_id": "$id",
                "first_name": {"$first": "$first_name"},
                "username":   {"$first": "$username"},
                "count":      {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    return await user_collection.aggregate(pipeline).to_list(length=limit)


# ── command ───────────────────────────────────────────────────────────────────

async def check(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /check <character_id>")
        return

    char_id = context.args[0].strip()

    # Support both numeric and string IDs
    char = await collection.find_one({"id": char_id})
    if not char:
        # try numeric match
        try:
            char = await collection.find_one({"id": int(char_id)})
        except (ValueError, TypeError):
            pass

    if not char:
        await update.message.reply_text(f"❌ Character <code>{escape(char_id)}</code> not found.",
                                        parse_mode=ParseMode.HTML)
        return

    cid        = str(char.get("id", "??"))
    name       = escape(char.get("name", "Unknown"))
    anime      = escape(char.get("anime", "Unknown"))
    rarity     = char.get("rarity", "Unknown")
    img_url    = char.get("img_url", "")
    global_cnt = char.get("claimed_count", 0)

    rar_display = _rarity_display(rarity)

    # ── Top catchers ─────────────────────────────────────────────────────────
    catchers = await _top_catchers(cid)

    catcher_lines = ""
    for row in catchers:
        uid  = row["_id"]
        fn   = escape(row.get("first_name") or "Unknown User")
        uname = row.get("username")
        cnt  = row["count"]
        if uname:
            mention = f'<a href="tg://user?id={uid}">{fn}</a>'
        else:
            mention = f'<a href="tg://user?id={uid}">{fn}</a> ({uid})'
        catcher_lines += f"  ➜ {mention} x{cnt}\n"

    if not catcher_lines:
        catcher_lines = "  <i>No one has caught this yet!</i>\n"

    caption = (
        f"OwO! Check out this character!\n\n"
        f"<b>{anime}</b>\n"
        f"<b>{cid}</b>: {name}\n"
        f"(<i>🌟 {rar_display}</i>)\n\n"
        f"🌍 <b>CAUGHT GLOBALLY: {global_cnt} times</b>\n\n"
        f"🥇 <b>TOP 10 CATCHERS OF THIS CHARACTER!</b>\n"
        f"{catcher_lines}"
    )

    if img_url:
        try:
            await update.message.reply_photo(
                photo=img_url,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as e:
            LOGGER.warning("check: failed to send photo for %s: %s", cid, e)

    # Fallback text only
    await update.message.reply_text(caption, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("check", check, block=False))
