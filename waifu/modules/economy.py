"""
modules/economy.py — Daily coins, balance, and marketplace.
"""
import math
import time
from bson import ObjectId
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, market_collection
from waifu.config import Config

_DAILY_COOLDOWN = 86_400   # 24 hours in seconds
_PAGE = 8

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(secs: int) -> str:
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


async def _ensure_user(user_id: int, u) -> dict:
    doc = await user_collection.find_one({"id": user_id})
    if not doc:
        doc = {
            "id": user_id, "username": u.username,
            "first_name": u.first_name, "characters": [],
            "coins": 0, "xp": 0, "wins": 0,
            "total_guesses": 0, "favorites": [],
        }
        await user_collection.insert_one(doc)
    return doc


# ── /balance ──────────────────────────────────────────────────────────────────

async def balance(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)
    await update.message.reply_text(
        f"💰 <b>{escape(u.first_name)}'s Balance</b>\n\n"
        f"Coins: <b>{doc.get('coins', 0):,}</b> 🪙",
        parse_mode=ParseMode.HTML,
    )


# ── /daily ────────────────────────────────────────────────────────────────────

async def daily(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)
    now = time.time()
    last = doc.get("last_daily", 0)

    if now - last < _DAILY_COOLDOWN:
        remaining = int(_DAILY_COOLDOWN - (now - last))
        await update.message.reply_text(
            f"⏳ Daily already claimed!\nCome back in <b>{_fmt_time(remaining)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    reward = Config.DAILY_COINS
    await user_collection.update_one(
        {"id": u.id},
        {"$inc": {"coins": reward}, "$set": {"last_daily": now}},
    )
    await update.message.reply_text(
        f"🎁 <b>Daily reward!</b>\n\n"
        f"You received <b>{reward:,} coins</b> 🪙\n"
        f"Current balance: <b>{doc.get('coins', 0) + reward:,}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /sell ─────────────────────────────────────────────────────────────────────

async def sell(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: <code>/sell [char_id] [price]</code>", parse_mode=ParseMode.HTML)
        return

    char_id, price_str = context.args
    if not price_str.isdigit() or int(price_str) <= 0:
        await update.message.reply_text("❌ Price must be a positive number.")
        return
    price = int(price_str)

    doc = await user_collection.find_one({"id": u.id})
    if not doc:
        await update.message.reply_text("❌ You have no characters.")
        return

    char = next((c for c in doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await update.message.reply_text("❌ That character isn't in your collection.")
        return

    # Remove from user's harem (escrow while listed)
    await user_collection.update_one(
        {"id": u.id},
        {"$pull": {"characters": {"id": char_id}}},
    )
    listing = {
        "seller_id":   u.id,
        "seller_name": u.first_name,
        "char_id":     char_id,
        "char":        char,
        "price":       price,
        "listed_at":   time.time(),
    }
    result = await market_collection.insert_one(listing)

    await update.message.reply_text(
        f"🏪 <b>{escape(char['name'])}</b> listed for <b>{price:,} coins</b>!\n"
        f"Listing ID: <code>{result.inserted_id}</code>",
        parse_mode=ParseMode.HTML,
    )


# ── /market ───────────────────────────────────────────────────────────────────

async def market(update: Update, context: CallbackContext, page: int = 0) -> None:
    args  = context.args if hasattr(context, "args") and context.args else []
    if args and args[0].isdigit():
        page = int(args[0]) - 1

    total   = await market_collection.count_documents({})
    if total == 0:
        await update.message.reply_text("🏪 The market is empty right now.")
        return

    total_pages = max(1, math.ceil(total / _PAGE))
    page = max(0, min(page, total_pages - 1))

    listings = await market_collection.find({}).sort("price", 1).skip(page * _PAGE).limit(_PAGE).to_list(_PAGE)

    lines = [f"🏪 <b>Market</b>  (page {page+1}/{total_pages})\n"]
    for lst in listings:
        char  = lst["char"]
        lines.append(
            f"{char.get('rarity','🎴')}  <b>{escape(char['name'])}</b>  "
            f"— <b>{lst['price']:,} 🪙</b>\n"
            f"   Seller: {escape(lst['seller_name'])}  |  "
            f"<code>/buy {lst['_id']}</code>"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"market:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"market:{page+1}"))

    kb = InlineKeyboardMarkup([nav] if nav else [])
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)


async def market_page_cb(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[1])

    total       = await market_collection.count_documents({})
    total_pages = max(1, math.ceil(total / _PAGE))
    page        = max(0, min(page, total_pages - 1))
    listings    = await market_collection.find({}).sort("price", 1).skip(page * _PAGE).limit(_PAGE).to_list(_PAGE)

    lines = [f"🏪 <b>Market</b>  (page {page+1}/{total_pages})\n"]
    for lst in listings:
        char = lst["char"]
        lines.append(
            f"{char.get('rarity','🎴')}  <b>{escape(char['name'])}</b>  "
            f"— <b>{lst['price']:,} 🪙</b>\n"
            f"   Seller: {escape(lst['seller_name'])}  |  "
            f"<code>/buy {lst['_id']}</code>"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"market:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"market:{page+1}"))
    kb = InlineKeyboardMarkup([nav])
    try:
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass


# ── /buy ──────────────────────────────────────────────────────────────────────

async def buy(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: <code>/buy [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid listing ID.")
        return

    listing = await market_collection.find_one({"_id": oid})
    if not listing:
        await update.message.reply_text("❌ Listing not found or already sold.")
        return
    if listing["seller_id"] == u.id:
        await update.message.reply_text("❌ You can't buy your own listing.")
        return

    buyer = await user_collection.find_one({"id": u.id})
    if not buyer or buyer.get("coins", 0) < listing["price"]:
        await update.message.reply_text(
            f"❌ Not enough coins. Need <b>{listing['price']:,}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Atomic exchange
    await user_collection.update_one({"id": u.id},
        {"$inc": {"coins": -listing["price"]},
         "$push": {"characters": listing["char"]}})
    await user_collection.update_one({"id": listing["seller_id"]},
        {"$inc": {"coins": listing["price"]}})
    await market_collection.delete_one({"_id": oid})

    char = listing["char"]
    await update.message.reply_text(
        f"✅ You bought <b>{escape(char['name'])}</b> for <b>{listing['price']:,} 🪙</b>!",
        parse_mode=ParseMode.HTML,
    )


# ── /delist ───────────────────────────────────────────────────────────────────

async def delist(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: <code>/delist [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid listing ID.")
        return

    listing = await market_collection.find_one({"_id": oid})
    if not listing:
        await update.message.reply_text("❌ Listing not found.")
        return
    if listing["seller_id"] != u.id:
        await update.message.reply_text("❌ That's not your listing.")
        return

    await market_collection.delete_one({"_id": oid})
    await user_collection.update_one(
        {"id": u.id}, {"$push": {"characters": listing["char"]}})
    await update.message.reply_text("✅ Listing removed. Character returned to your harem.")


application.add_handler(CommandHandler("balance", balance, block=False))
application.add_handler(CommandHandler("daily",   daily,   block=False))
application.add_handler(CommandHandler("sell",    sell,    block=False))
application.add_handler(CommandHandler("market",  market,  block=False))
application.add_handler(CommandHandler("buy",     buy,     block=False))
application.add_handler(CommandHandler("delist",  delist,  block=False))
application.add_handler(CallbackQueryHandler(market_page_cb, pattern=r"^market:\d+$", block=False))
