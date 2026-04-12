"""
modules/bmarket.py — Black Material Market.

Owner-only management:
  /blackmaterial <stock>  — set available BM stock (e.g. /blackmaterial 50)
  /blackmaterial <stock> <price>  — override price (default 3000 coins/unit)

User commands:
  /bmarket  — view store & buy Black Material with coins
"""
import math
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, bm_market_collection, user_collection, OWNER_ID, sudo_users

_STORE_ID      = "bm_store"
_DEFAULT_PRICE = 3_000   # coins per Black Material unit
_MAX_PER_BUY   = 10      # max units a user can buy in one transaction


async def _get_store() -> dict:
    doc = await bm_market_collection.find_one({"_id": _STORE_ID})
    if not doc:
        doc = {"_id": _STORE_ID, "stock": 0, "price": _DEFAULT_PRICE}
    return doc


# ── /blackmaterial <stock> [price] — owner/sudo only ─────────────────────────

async def blackmaterial_cmd(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    if uid not in {OWNER_ID, *sudo_users}:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ ဒီ command ကို PM မှာသာ သုံးနိုင်သည်.")
        return

    args = context.args
    if not args:
        store = await _get_store()
        await update.message.reply_text(
            f"🔩 <b>Black Material Store</b>\n\n"
            f"📦 Stock: <b>{store['stock']}</b> units\n"
            f"💰 Price: <b>{store['price']:,} 🪙</b> per unit\n\n"
            f"Usage: /blackmaterial &lt;stock&gt; [price]\n"
            f"Example: /blackmaterial 50\n"
            f"         /blackmaterial 50 2500",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        new_stock = int(args[0])
        new_price = int(args[1]) if len(args) > 1 else None
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Usage: /blackmaterial <stock> [price]")
        return

    if new_stock < 0:
        await update.message.reply_text("❌ Stock 0 ထက်မနည်းရ.")
        return

    update_fields: dict = {"stock": new_stock}
    if new_price is not None:
        if new_price <= 0:
            await update.message.reply_text("❌ Price 0 ထက်ကြီးရမည်.")
            return
        update_fields["price"] = new_price

    await bm_market_collection.update_one(
        {"_id": _STORE_ID},
        {"$set": update_fields},
        upsert=True,
    )

    store = await _get_store()
    await update.message.reply_text(
        f"✅ <b>BM Store Updated!</b>\n\n"
        f"📦 Stock: <b>{store['stock']}</b> units\n"
        f"💰 Price: <b>{store['price']:,} 🪙</b> per unit",
        parse_mode=ParseMode.HTML,
    )


# ── /bmarket — anyone ─────────────────────────────────────────────────────────

async def bmarket_cmd(update: Update, context: CallbackContext) -> None:
    store   = await _get_store()
    stock   = store["stock"]
    price   = store["price"]
    user_id = update.effective_user.id

    user = await user_collection.find_one({"id": user_id}, {"coins": 1, "black_material": 1})
    coins = (user or {}).get("coins", 0)
    bm    = (user or {}).get("black_material", 0)

    can_afford = coins // price if price > 0 else 0
    max_buy    = min(stock, can_afford, _MAX_PER_BUY)

    text = (
        f"🔩 <b>Black Material Market</b>\n\n"
        f"📦 Available: <b>{stock}</b> units\n"
        f"💰 Price: <b>{price:,} 🪙</b> / unit\n\n"
        f"👤 Your Coins: <b>{coins:,} 🪙</b>\n"
        f"🔩 Your BM: <b>{bm}</b>\n\n"
    )

    if stock == 0:
        text += "⚠️ <i>재고 없음 — Out of stock!</i>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    if coins < price:
        text += f"❌ <i>Coins မလုံ! ({price:,} 🪙 လိုသည်)</i>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    text += "🛒 <b>ဝယ်မည့် ပမာဏ ရွေးပါ:</b>"

    # Build buy buttons (1, 2, 3, 5, 10 — only if affordable & in stock)
    options = [1, 2, 3, 5, 10]
    btns = []
    row  = []
    for qty in options:
        if qty > stock or qty * price > coins:
            continue
        row.append(InlineKeyboardButton(
            f"×{qty}  ({qty * price:,} 🪙)",
            callback_data=f"bm:buy:{qty}",
        ))
        if len(row) == 2:
            btns.append(row)
            row = []
    if row:
        btns.append(row)

    if not btns:
        text += f"\n❌ <i>Coins မလုံ!</i>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(btns),
        parse_mode=ParseMode.HTML,
    )


# ── Buy callback ──────────────────────────────────────────────────────────────

async def _bm_callback(update: Update, context: CallbackContext) -> None:
    q       = update.callback_query
    user_id = q.from_user.id
    data    = q.data   # bm:buy:<qty>

    await q.answer()

    parts = data.split(":")
    if len(parts) < 3 or parts[1] != "buy":
        return

    try:
        qty = int(parts[2])
    except ValueError:
        return

    if qty <= 0 or qty > _MAX_PER_BUY:
        await q.answer("❌ Invalid quantity.", show_alert=True)
        return

    store = await _get_store()
    stock = store["stock"]
    price = store["price"]
    total = qty * price

    if stock < qty:
        await q.answer(f"❌ Stock မလုံ! (ကျန်: {stock})", show_alert=True)
        return

    user = await user_collection.find_one({"id": user_id}, {"coins": 1, "first_name": 1})
    coins = (user or {}).get("coins", 0)

    if coins < total:
        await q.answer(f"❌ Coins မလုံ! ({coins:,}/{total:,} 🪙)", show_alert=True)
        return

    # Deduct coins, add BM, decrement stock
    await user_collection.update_one(
        {"id": user_id},
        {
            "$inc": {"coins": -total, "black_material": qty},
            "$setOnInsert": {"wanted_coins": 0, "black_material": 0, "badges": []},
        },
        upsert=True,
    )
    await bm_market_collection.update_one(
        {"_id": _STORE_ID},
        {"$inc": {"stock": -qty}},
    )

    updated_user  = await user_collection.find_one({"id": user_id}, {"coins": 1, "black_material": 1})
    new_coins     = (updated_user or {}).get("coins", 0)
    new_bm        = (updated_user or {}).get("black_material", 0)

    await q.edit_message_text(
        f"✅ <b>Purchase Successful!</b>\n\n"
        f"🔩 Black Material: <b>+{qty}</b>  (Total: {new_bm} 🔩)\n"
        f"💰 Coins spent: <b>{total:,} 🪙</b>  (Balance: {new_coins:,} 🪙)",
        parse_mode=ParseMode.HTML,
    )


# ── Register ──────────────────────────────────────────────────────────────────

application.add_handler(CommandHandler("blackmaterial", blackmaterial_cmd, block=False))
application.add_handler(CommandHandler("bmarket",       bmarket_cmd,       block=False))
application.add_handler(CallbackQueryHandler(_bm_callback, pattern=r"^bm:buy:", block=False))
