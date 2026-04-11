"""
modules/economy.py — Daily coins, balance, and marketplace.

Market is card-style: one listing per page with character photo.
"""
import math
import time
from bson import ObjectId
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, market_collection
from waifu.config import Config

_DAILY_COOLDOWN = 86_400   # 24 hours in seconds


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


# ── Rarity badge map ──────────────────────────────────────────────────────────

_RARITY_BADGE = {
    "⚪ Common":            "⚪",
    "🟣 Rare":              "🟣",
    "🟡 Legendary":         "🟡",
    "🔮 Mythical":          "🔮",
    "💮 Special Edition":   "💮",
    "🌌 Universal Limited": "🌌",
}


# ── Market card builder ───────────────────────────────────────────────────────

async def _market_card(page: int) -> tuple[str, str | None, InlineKeyboardMarkup] | None:
    """
    Returns (caption, photo_file_id_or_none, keyboard) for a market page,
    or None if market is empty.
    """
    total = await market_collection.count_documents({})
    if total == 0:
        return None

    page = max(0, min(page, total - 1))
    lst  = await market_collection.find({}).sort("price", 1).skip(page).limit(1).to_list(1)
    if not lst:
        return None

    listing = lst[0]
    char    = listing["char"]
    rarity  = char.get("rarity", "?")
    badge   = _RARITY_BADGE.get(rarity, "🎴")

    caption = (
        f"🏪 <b>Market</b>  [{page+1} / {total}]\n\n"
        f"{badge} <b>{escape(char['name'])}</b>\n"
        f"📺 Aɴɪᴍᴇ: {escape(char.get('anime', '?'))}\n"
        f"✨ Rᴀʀɪᴛʏ: {rarity}\n\n"
        f"💰 Price: <b>{listing['price']:,} 🪙</b>\n"
        f"👤 Seller: <b>{escape(listing['seller_name'])}</b>\n"
        f"🆔 <code>{listing['_id']}</code>"
    )

    # Nav row
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"mkt:page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1} / {total}", callback_data="noop"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"mkt:page:{page+1}"))

    buy_btn = InlineKeyboardButton(
        f"🛒 Buy  {listing['price']:,} 🪙",
        callback_data=f"mkt:buy:{listing['_id']}:{page}",
    )
    kb = InlineKeyboardMarkup([nav, [buy_btn]])

    photo = char.get("img_url")
    return caption, photo, kb


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
    args = context.args if hasattr(context, "args") and context.args else []
    if args and args[0].isdigit():
        page = int(args[0]) - 1

    result = await _market_card(page)
    if not result:
        await update.message.reply_text("🏪 The market is empty right now.")
        return

    caption, photo, kb = result

    if photo:
        try:
            await update.message.reply_photo(
                photo=photo, caption=caption,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
            return
        except Exception:
            pass

    await update.message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)


# ── Market callback (page nav + inline buy) ───────────────────────────────────

async def market_cb(update: Update, context: CallbackContext) -> None:
    q    = update.callback_query
    uid  = q.from_user.id
    data = q.data   # mkt:page:<n> | mkt:buy:<oid>:<page>

    parts = data.split(":")

    # ── Page navigation ───────────────────────────────────────────────────────
    if parts[1] == "page":
        await q.answer()
        page   = int(parts[2])
        result = await _market_card(page)
        if not result:
            await q.answer("Market is empty!", show_alert=True)
            return

        caption, photo, kb = result

        if photo:
            try:
                await q.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML),
                    reply_markup=kb,
                )
                return
            except Exception:
                pass
        try:
            await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            try:
                await q.edit_message_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception:
                pass
        return

    # ── Inline buy ────────────────────────────────────────────────────────────
    if parts[1] == "buy":
        listing_id_str = parts[2]
        back_page      = int(parts[3]) if len(parts) > 3 else 0

        try:
            oid = ObjectId(listing_id_str)
        except Exception:
            await q.answer("❌ Invalid listing ID.", show_alert=True)
            return

        listing = await market_collection.find_one({"_id": oid})
        if not listing:
            await q.answer("❌ Listing not found or already sold!", show_alert=True)
            # Refresh to next available page
            total = await market_collection.count_documents({})
            if total == 0:
                try:
                    await q.edit_message_caption(
                        caption="🏪 The market is now empty.",
                        reply_markup=InlineKeyboardMarkup([]),
                    )
                except Exception:
                    pass
            else:
                new_page = min(back_page, total - 1)
                result   = await _market_card(new_page)
                if result:
                    caption, photo, kb = result
                    if photo:
                        try:
                            await q.edit_message_media(
                                media=InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML),
                                reply_markup=kb,
                            )
                            return
                        except Exception:
                            pass
                    try:
                        await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
                    except Exception:
                        pass
            return

        if listing["seller_id"] == uid:
            await q.answer("❌ You can't buy your own listing!", show_alert=True)
            return

        buyer = await user_collection.find_one({"id": uid})
        coins = buyer.get("coins", 0) if buyer else 0
        if coins < listing["price"]:
            await q.answer(
                f"❌ Coins မလုံ! ({coins:,} / {listing['price']:,} 🪙)",
                show_alert=True,
            )
            return

        # Atomic exchange
        await user_collection.update_one(
            {"id": uid},
            {"$inc": {"coins": -listing["price"]}, "$push": {"characters": listing["char"]}},
        )
        await user_collection.update_one(
            {"id": listing["seller_id"]},
            {"$inc": {"coins": listing["price"]}},
        )
        await market_collection.delete_one({"_id": oid})

        char = listing["char"]
        await q.answer(
            f"✅ {char['name']} ကို {listing['price']:,} 🪙 နဲ့ ဝယ်ပြီး!",
            show_alert=True,
        )

        # Refresh card view to next listing
        total = await market_collection.count_documents({})
        if total == 0:
            try:
                await q.edit_message_caption(
                    caption="🏪 The market is now empty.",
                    reply_markup=InlineKeyboardMarkup([]),
                )
            except Exception:
                pass
        else:
            new_page = min(back_page, total - 1)
            result   = await _market_card(new_page)
            if result:
                caption, photo, kb = result
                if photo:
                    try:
                        await q.edit_message_media(
                            media=InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML),
                            reply_markup=kb,
                        )
                        return
                    except Exception:
                        pass
                try:
                    await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
                except Exception:
                    pass


# ── /buy (direct by listing ID) ───────────────────────────────────────────────

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

    await user_collection.update_one(
        {"id": u.id},
        {"$inc": {"coins": -listing["price"]}, "$push": {"characters": listing["char"]}},
    )
    await user_collection.update_one(
        {"id": listing["seller_id"]},
        {"$inc": {"coins": listing["price"]}},
    )
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


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler("balance", balance, block=False))
application.add_handler(CommandHandler("daily",   daily,   block=False))
application.add_handler(CommandHandler("sell",    sell,    block=False))
application.add_handler(CommandHandler("market",  market,  block=False))
application.add_handler(CommandHandler("buy",     buy,     block=False))
application.add_handler(CommandHandler("delist",  delist,  block=False))
application.add_handler(CallbackQueryHandler(market_cb, pattern=r"^mkt:", block=False))
