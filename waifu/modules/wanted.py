"""
modules/wanted.py — /wanted <char_id>

Shows who owns a character + their star levels + Wanted Coin values.
Works via inline like /check.
"""
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, user_collection

# Wanted Coin values per rarity per star level (displayed, not awarded here)
_WC_TABLE: dict[str, dict[int, int]] = {
    "🌐 Global": {
        0: 600,
        1: 3_000,
        2: 4_000,
        3: 6_000,
    },
    "💮 Special Edition": {
        0: 1_000,
        1: 5_000,
        2: 10_000,
        3: 20_000,
    },
    "🌌 Universal Limited": {
        0: 3_000,
        1: 20_000,
        2: 40_000,
        3: 100_000,
    },
}

_PREMIUM = set(_WC_TABLE.keys())


def _stars(n: int) -> str:
    return "★" * n + "☆" * (3 - n)


async def wanted(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /wanted <character_id>")
        return

    char_id = context.args[0].strip()

    char = await collection.find_one({"id": char_id})
    if not char:
        try:
            char = await collection.find_one({"id": int(char_id)})
        except (ValueError, TypeError):
            pass

    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{escape(char_id)}</code> not found.",
            parse_mode=ParseMode.HTML,
        )
        return

    cid      = str(char.get("id", "??"))
    name     = escape(char.get("name", "Unknown"))
    anime    = escape(char.get("anime", "Unknown"))
    rarity   = char.get("rarity", "Unknown")
    img_url  = char.get("img_url", "")
    parts    = rarity.split(" ", 1)
    rar_emoji = parts[0] if parts else "🌟"
    rar_name  = parts[1] if len(parts) > 1 else rarity

    wc_table = _WC_TABLE.get(rarity, {})
    is_premium = rarity in _PREMIUM

    # ── Find all owners ───────────────────────────────────────────────────────
    pipeline = [
        {"$match": {"characters.id": cid}},
        {"$project": {
            "id": 1, "first_name": 1, "username": 1,
            "waifu_stars": 1,
        }},
    ]
    owners_raw = await user_collection.aggregate(pipeline).to_list(length=200)

    # Sort by star count desc
    def _star_key(doc: dict) -> int:
        return doc.get("waifu_stars", {}).get(cid, 0)

    owners_raw.sort(key=_star_key, reverse=True)

    # Build owners text
    owner_lines = ""
    for i, doc in enumerate(owners_raw[:20], 1):
        uid  = doc["id"]
        fn   = escape(doc.get("first_name") or "Unknown")
        star = doc.get("waifu_stars", {}).get(cid, 0)
        wc   = wc_table.get(star, 0)
        wc_part = f" | 💰 {wc:,} WC" if is_premium and wc else ""
        owner_lines += f"  {i}. <a href='tg://user?id={uid}'>{fn}</a> {_stars(star)}{wc_part}\n"

    if not owner_lines:
        owner_lines = "  <i>No one owns this character yet!</i>\n"

    # Wanted Coin table for premium rarities
    wc_info = ""
    if is_premium and wc_table:
        wc_info = "\n📋 <b>Wanted Coin Table:</b>\n"
        labels = {0: "No Star", 1: "1★", 2: "2★", 3: "3★"}
        for lvl, val in sorted(wc_table.items()):
            wc_info += f"  {labels[lvl]} → {val:,} WC\n"

    caption = (
        f"🎯 <b>WANTED</b>\n\n"
        f"╭──\n"
        f"├─➩ {anime}\n"
        f"├─➩ <b>{cid}</b>: {name}\n"
        f"├─➩ RARITY: {rar_emoji} {rar_name}\n"
        f"{wc_info}\n"
        f"👥 <b>OWNERS ({len(owners_raw)}):</b>\n"
        f"{owner_lines}"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🔍 Check Character",
            switch_inline_query_current_chat=f"",
        )
    ]]) if False else None  # no inline button for now

    if img_url:
        try:
            await update.message.reply_photo(
                photo=img_url,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception:
            pass

    await update.message.reply_text(caption, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("wanted", wanted, block=False))
