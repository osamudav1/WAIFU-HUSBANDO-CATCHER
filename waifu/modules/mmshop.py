"""
modules/mmshop.py — Myanmar Kyat (MMK) character shop.

Commands:
  /mmshop                              show shop (user) / owner panel (owner)
  /mmshop <char_id> <mmk> [copies]     owner: list character
  /setphone kpay|wave <09xxxxxxx>      owner: set payment phone numbers
  /mmremove <listing_id>               owner: remove listing

Buy flow (PM only):
  1. User clicks "💳 To Buy"
  2. Bot DMs card info + [💚 Kpay | 🌊 Wave]
  3. User picks payment → owner phone shown + ask receipt photo
  4. User sends receipt photo
  5. Bot forwards to owner DM with card/buyer info + [✅ Confirm | ❌ Cancel]
  6. Owner confirms → buyer notified + card image sent
  7. Owner cancels → buyer notified
"""
from __future__ import annotations

import asyncio
import random
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackContext, CallbackQueryHandler, CommandHandler,
    MessageHandler, filters,
)

from waifu import (
    application, OWNER_ID, sudo_users,
    mmshop_listings_collection, mmshop_orders_collection,
    bot_settings_collection,
    collection as char_collection,
)
from waifu.config import Config

_PAGE_SIZE = 4

# user_id → {order_id, char_name, mmk_price, pay_type}
_pending_receipt: dict[int, dict] = {}

# ── MMK Drop state ───────────────────────────────────────────────────────────
_mm_msg_count:    dict[int, int]   = {}   # chat_id → msg count
_mm_active_drop:  dict[int, dict]  = {}   # chat_id → active listing doc
_mm_last_drop:    dict[int, float] = {}   # chat_id → timestamp of last drop
_mm_unique_users: dict[int, dict]  = {}   # chat_id → {user_id: last_msg_ts}

_MM_MSG_THRESHOLD = 2000    # messages needed to trigger a drop
_MM_COOLDOWN_SECS = 5       # seconds between consecutive drops
_MM_MIN_MEMBERS   = 3000    # minimum group member count
_MM_MIN_ACTIVE    = 200     # minimum unique active users (last 1 hour)
_MM_EXPIRE_SECS   = 60      # seconds before drop card expires


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_owner(uid: int) -> bool:
    return uid == OWNER_ID or uid in sudo_users


async def _get_phone(kind: str) -> str:
    doc = await bot_settings_collection.find_one({"_id": f"mm_{kind}_phone"})
    return (doc or {}).get("value", "")


async def _set_phone(kind: str, number: str) -> None:
    await bot_settings_collection.update_one(
        {"_id": f"mm_{kind}_phone"},
        {"$set": {"value": number}},
        upsert=True,
    )


async def _find_listing(listing_id: str) -> dict | None:
    try:
        from bson import ObjectId
        doc = await mmshop_listings_collection.find_one({"_id": ObjectId(listing_id)})
        if doc:
            return doc
    except Exception:
        pass
    return await mmshop_listings_collection.find_one({"_id": listing_id})


async def _find_order(order_id: str) -> dict | None:
    try:
        from bson import ObjectId
        doc = await mmshop_orders_collection.find_one({"_id": ObjectId(order_id)})
        if doc:
            return doc
    except Exception:
        pass
    return await mmshop_orders_collection.find_one({"_id": order_id})


# ── shop page ─────────────────────────────────────────────────────────────────

async def _send_page(update: Update, context: CallbackContext, page: int) -> None:
    all_listings = await mmshop_listings_collection.find(
        {"sold_out": {"$ne": True}}
    ).sort("listed_at", -1).to_list(1000)

    total = len(all_listings)
    if total == 0:
        await update.effective_message.reply_text(
            "🏪 <b>MMK Shop</b>\n\nလောလောဆယ် listing မရှိသေး။",
            parse_mode=ParseMode.HTML,
        )
        return

    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _PAGE_SIZE
    page_items = all_listings[start: start + _PAGE_SIZE]

    for li in page_items:
        lid    = str(li["_id"])
        name   = li.get("char_name",  "Unknown")
        anime  = li.get("char_anime", "Unknown")
        rarity = li.get("char_rarity","Unknown")
        price  = li.get("mmk_price",  0)
        copies = li.get("copies",     0)
        sold   = li.get("sold_count", 0)
        img    = li.get("img_url",    "")

        stock = "♾️ Unlimited" if copies == 0 else f"{copies - sold} / {copies} ကျန်"
        cap = (
            f"🌸 <b>{escape(name)}</b>\n"
            f"📺 {escape(anime)}\n"
            f"💎 {escape(rarity)}\n\n"
            f"💵 Price: <b>{price:,} MMK</b>\n"
            f"📦 Stock: {stock}"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("💳 To Buy", callback_data=f"mm_buy_{lid}"),
        ]])
        try:
            if li.get("media_type") == "video":
                await update.effective_message.reply_video(
                    img, caption=cap, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await update.effective_message.reply_photo(
                    img, caption=cap, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            await update.effective_message.reply_text(
                cap, parse_mode=ParseMode.HTML, reply_markup=kb)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"mm_page_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="mm_noop"))
    if start + _PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"mm_page_{page + 1}"))

    if len(page_items) > 0:
        await update.effective_message.reply_text(
            f"🏪 <b>MMK Shop</b>  •  Page {page + 1}/{total_pages}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([nav]),
        )


# ── owner panel ───────────────────────────────────────────────────────────────

async def _owner_panel(update: Update, context: CallbackContext) -> None:
    kpay = await _get_phone("kpay")
    wave = await _get_phone("wave")
    n_list   = await mmshop_listings_collection.count_documents({})
    n_pending = await mmshop_orders_collection.count_documents({"status": "pending_confirm"})

    text = (
        "🏪 <b>MMK Shop — Owner Panel</b>\n\n"
        f"💚 Kpay: <code>{kpay or '— မသတ်မှတ်ရသေး —'}</code>\n"
        f"🌊 Wave: <code>{wave or '— မသတ်မှတ်ရသေး —'}</code>\n\n"
        f"📋 Listings: <b>{n_list}</b>\n"
        f"⏳ Pending orders: <b>{n_pending}</b>\n\n"
        "<b>Commands:</b>\n"
        "<code>/mmshop &lt;char_id&gt; &lt;mmk&gt; [copies]</code> — list\n"
        "<code>/mmremove &lt;listing_id&gt;</code> — remove listing\n"
        "<code>/setphone kpay &lt;09xxx&gt;</code> — Kpay number\n"
        "<code>/setphone wave &lt;09xxx&gt;</code> — Wave number"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Listings",  callback_data="mm_owner_listings"),
         InlineKeyboardButton("🛒 View Shop", callback_data="mm_page_0")],
    ])
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ── /mmshop ───────────────────────────────────────────────────────────────────

async def mmshop_cmd(update: Update, context: CallbackContext) -> None:
    uid  = update.effective_user.id
    args = context.args or []

    if not args:
        if _is_owner(uid):
            await _owner_panel(update, context)
        else:
            await _send_page(update, context, 0)
        return

    if not _is_owner(uid):
        await update.message.reply_text("❌ Owner only.")
        return

    if len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/mmshop &lt;char_id&gt; &lt;mmk_price&gt; [copies]</code>\n"
            "copies = 0 → unlimited",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        mmk_price = int(args[1].replace(",", ""))
        copies    = int(args[2]) if len(args) > 2 else 0
    except ValueError:
        await update.message.reply_text("❌ MMK price နဲ့ copies numbers ဖြစ်ရမည်။")
        return

    # Character IDs are stored as strings in DB (e.g. "30"), strip leading zeros
    char_id = args[0].strip().lstrip("0") or args[0].strip()

    char = await char_collection.find_one({"id": char_id})
    if not char:
        # fallback: try integer form
        try:
            char = await char_collection.find_one({"id": int(char_id)})
        except Exception:
            pass
    if not char:
        await update.message.reply_text(f"❌ Character ID {char_id} မတွေ့ပါ။")
        return

    li = {
        "char_id":    char.get("id", char_id),   # use exact id from DB
        "char_name":  char.get("name",       "Unknown"),
        "char_anime": char.get("anime",      "Unknown"),
        "char_rarity":char.get("rarity",     "Unknown"),
        "img_url":    char.get("img_url",    ""),
        "media_type": char.get("media_type", "photo"),
        "mmk_price":  mmk_price,
        "copies":     copies,
        "sold_count": 0,
        "listed_at":  time.time(),
        "sold_out":   False,
    }
    res = await mmshop_listings_collection.insert_one(li)
    lid = str(res.inserted_id)

    copies_txt = "♾️ Unlimited" if copies == 0 else str(copies)
    await update.message.reply_text(
        f"✅ <b>MMK Shop တင်ပြီးပါပြီ!</b>\n\n"
        f"🌸 {escape(char.get('name', ''))}\n"
        f"💵 {mmk_price:,} MMK\n"
        f"📦 Copies: {copies_txt}\n"
        f"🆔 Listing: <code>{lid}</code>",
        parse_mode=ParseMode.HTML,
    )


# ── /mmremove ─────────────────────────────────────────────────────────────────

async def mmremove_cmd(update: Update, context: CallbackContext) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Owner only.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: <code>/mmremove &lt;listing_id&gt;</code>", parse_mode=ParseMode.HTML)
        return
    li = await _find_listing(args[0])
    if not li:
        await update.message.reply_text("❌ Listing မတွေ့ပါ။")
        return
    await mmshop_listings_collection.delete_one({"_id": li["_id"]})
    await update.message.reply_text(f"✅ {escape(li.get('char_name','?'))} listing ဖျက်ပြီးပါပြီ။", parse_mode=ParseMode.HTML)


# ── /setphone ─────────────────────────────────────────────────────────────────

async def setphone_cmd(update: Update, context: CallbackContext) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Owner only.")
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "<code>/setphone kpay 09xxxxxxx</code>\n"
            "<code>/setphone wave 09xxxxxxx</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    kind = args[0].lower()
    if kind not in ("kpay", "wave"):
        await update.message.reply_text("❌ kpay သို့မဟုတ် wave ဖြစ်ရမည်။")
        return
    number = args[1].strip()
    await _set_phone(kind, number)
    emoji = "💚" if kind == "kpay" else "🌊"
    await update.message.reply_text(
        f"{emoji} <b>{kind.upper()} number:</b> <code>{number}</code>",
        parse_mode=ParseMode.HTML,
    )


# ── callback: To Buy ──────────────────────────────────────────────────────────

async def _cb_buy(cq, context: CallbackContext, listing_id: str) -> None:
    li = await _find_listing(listing_id)
    if not li:
        await cq.answer("❌ Listing မတွေ့ပါ။", show_alert=True); return
    if li.get("sold_out"):
        await cq.answer("❌ ကုန်သွားပြီ!", show_alert=True); return

    copies = li.get("copies", 0)
    sold   = li.get("sold_count", 0)
    if copies > 0 and sold >= copies:
        await cq.answer("❌ ကုန်သွားပြီ!", show_alert=True); return

    kpay = await _get_phone("kpay")
    wave = await _get_phone("wave")
    if not kpay and not wave:
        await cq.answer("⚠️ Owner က ဖုန်းနံပါတ် မသတ်မှတ်ရသေး။", show_alert=True); return

    stock_txt = "♾️ Unlimited" if copies == 0 else f"{copies - sold} ကျန်"
    cap = (
        f"🛍️ <b>MMK Shop — Purchase</b>\n\n"
        f"🌸 <b>{escape(li.get('char_name',''))}</b>\n"
        f"📺 {escape(li.get('char_anime',''))}\n"
        f"💎 {escape(li.get('char_rarity',''))}\n\n"
        f"💵 Price: <b>{li['mmk_price']:,} MMK</b>\n"
        f"📦 Stock: {stock_txt}\n\n"
        "Payment method ရွေးပါ:"
    )
    btns = []
    if kpay: btns.append(InlineKeyboardButton("💚 Kpay",  callback_data=f"mm_pay_kpay_{listing_id}"))
    if wave:  btns.append(InlineKeyboardButton("🌊 Wave",  callback_data=f"mm_pay_wave_{listing_id}"))
    kb = InlineKeyboardMarkup([btns])

    img = li.get("img_url", "")
    try:
        if li.get("media_type") == "video":
            await context.bot.send_video(cq.from_user.id, img, caption=cap, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await context.bot.send_photo(cq.from_user.id, img, caption=cap, parse_mode=ParseMode.HTML, reply_markup=kb)
        await cq.answer("✅ DM စစ်ပါ!")
    except Exception:
        await cq.answer("⚠️ Bot ကို DM ဦးစွာ /start ပို့ပါ!", show_alert=True)


# ── callback: Kpay / Wave ─────────────────────────────────────────────────────

async def _cb_pay(cq, context: CallbackContext, pay_type: str, listing_id: str) -> None:
    uid = cq.from_user.id
    li  = await _find_listing(listing_id)
    if not li:
        await cq.answer("❌ Listing မတွေ့ပါ။", show_alert=True); return

    phone = await _get_phone(pay_type)
    if not phone:
        await cq.answer(f"❌ {pay_type.upper()} ဖုန်းနံပါတ် မရှိသေး။", show_alert=True); return

    await cq.answer()

    order = {
        "listing_id":     listing_id,
        "buyer_id":       uid,
        "buyer_username": cq.from_user.username or "",
        "buyer_name":     cq.from_user.full_name or str(uid),
        "char_id":        li.get("char_id"),
        "char_name":      li.get("char_name", ""),
        "char_anime":     li.get("char_anime", ""),
        "char_rarity":    li.get("char_rarity", ""),
        "img_url":        li.get("img_url", ""),
        "media_type":     li.get("media_type", "photo"),
        "mmk_price":      li.get("mmk_price", 0),
        "payment_type":   pay_type,
        "status":         "pending_receipt",
        "created_at":     time.time(),
    }
    res      = await mmshop_orders_collection.insert_one(order)
    order_id = str(res.inserted_id)

    _pending_receipt[uid] = {
        "order_id":  order_id,
        "char_name": li.get("char_name", ""),
        "mmk_price": li.get("mmk_price", 0),
        "pay_type":  pay_type,
    }

    emoji = "💚" if pay_type == "kpay" else "🌊"
    await context.bot.send_message(
        uid,
        f"{emoji} <b>{pay_type.upper()} ဖုန်းနံပါတ်:</b>\n"
        f"<code>{phone}</code>\n\n"
        f"💵 ငွေပမာဏ: <b>{li['mmk_price']:,} MMK</b>\n\n"
        f"ငွေလွှဲပြီးရင် <b>ပြေစာ screenshot ဓာတ်ပုံ</b>\n"
        f"ဒီ chat မှာ သာ ပို့ပါ — Owner confirm ရင် card ရောက်မည်။",
        parse_mode=ParseMode.HTML,
    )


# ── receipt handler ───────────────────────────────────────────────────────────

async def _receipt_handler(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    if uid not in _pending_receipt:
        return

    msg = update.message
    if not msg:
        return

    if msg.photo:
        file_id  = msg.photo[-1].file_id
        send_fn  = "photo"
    elif msg.document:
        file_id  = msg.document.file_id
        send_fn  = "document"
    else:
        return

    pending  = _pending_receipt.pop(uid)
    order_id = pending["order_id"]
    char_name = pending["char_name"]
    mmk_price = pending["mmk_price"]
    pay_type  = pending["pay_type"]

    await mmshop_orders_collection.update_one(
        {"_id": order_id},
        {"$set": {"receipt_file_id": file_id, "status": "pending_confirm"}},
    )

    u     = update.effective_user
    uname = f"@{u.username}" if u.username else escape(u.full_name or str(uid))
    emoji = "💚" if pay_type == "kpay" else "🌊"

    owner_txt = (
        f"🛍️ <b>MMK Purchase — ပြေစာ ရောက်ပြီ</b>\n\n"
        f"👤 Buyer: {uname} (<code>{uid}</code>)\n"
        f"🌸 Card: <b>{escape(char_name)}</b>\n"
        f"💵 Price: <b>{mmk_price:,} MMK</b>\n"
        f"{emoji} Payment: {pay_type.upper()}\n\n"
        f"📄 ပြေစာ အောက်မှာပါသည် — Confirm/Cancel နှိပ်ပါ:"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"mm_confirm_{order_id}"),
        InlineKeyboardButton("❌ Cancel",  callback_data=f"mm_cancel_{order_id}"),
    ]])

    try:
        await context.bot.send_message(OWNER_ID, owner_txt, parse_mode=ParseMode.HTML)
        if send_fn == "photo":
            await context.bot.send_photo(OWNER_ID, file_id, reply_markup=kb)
        else:
            await context.bot.send_document(OWNER_ID, file_id, reply_markup=kb)
    except Exception as e:
        await msg.reply_text(f"⚠️ Owner ထံ မပို့နိုင်ပါ: {e}")
        return

    await msg.reply_text(
        "✅ <b>ပြေစာ ပို့ပြီးပါပြီ!</b>\n\nOwner confirm ရင် card ရောက်ပါမည်။",
        parse_mode=ParseMode.HTML,
    )


# ── confirm ───────────────────────────────────────────────────────────────────

async def _cb_confirm(cq, context: CallbackContext, order_id: str) -> None:
    if cq.from_user.id != OWNER_ID:
        await cq.answer("❌ Owner only.", show_alert=True); return
    await cq.answer()

    order = await _find_order(order_id)
    if not order:
        await context.bot.send_message(OWNER_ID, "❌ Order မတွေ့ပါ။"); return
    if order.get("status") == "confirmed":
        await cq.answer("✅ Already confirmed.", show_alert=True); return

    await mmshop_orders_collection.update_one(
        {"_id": order["_id"]},
        {"$set": {"status": "confirmed", "confirmed_at": time.time()}},
    )

    li = await _find_listing(order.get("listing_id", ""))
    if li:
        copies   = li.get("copies", 0)
        new_sold = li.get("sold_count", 0) + 1
        upd = {"sold_count": new_sold}
        if copies > 0 and new_sold >= copies:
            upd["sold_out"] = True
        await mmshop_listings_collection.update_one({"_id": li["_id"]}, {"$set": upd})

    buyer_id  = order["buyer_id"]
    char_name = order.get("char_name", "Unknown")
    mmk_price = order.get("mmk_price", 0)
    img_url    = order.get("img_url", "")
    media_type = order.get("media_type", "photo")

    buyer_txt = (
        f"🎉 <b>ဝယ်ယူမှု အတည်ပြုပြီးပါပြီ!</b>\n\n"
        f"🌸 <b>{escape(char_name)}</b> ကဒ် ရပြီပါပြီ!\n"
        f"💵 {mmk_price:,} MMK ပေးချေပြီးပါပြီ — ကျေးဇူးတင်ပါသည်!"
    )
    try:
        if img_url:
            if media_type == "video":
                await context.bot.send_video(buyer_id, img_url, caption=buyer_txt, parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_photo(buyer_id, img_url, caption=buyer_txt, parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(buyer_id, buyer_txt, parse_mode=ParseMode.HTML)
    except Exception as e:
        await context.bot.send_message(OWNER_ID, f"⚠️ Buyer ထံ မပို့နိုင်: {e}")

    await cq.edit_message_reply_markup(InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmed", callback_data="mm_noop"),
    ]]))
    await context.bot.send_message(OWNER_ID, "✅ Confirm ပြီး card ပို့ပြီးပါပြီ!")


# ── cancel ────────────────────────────────────────────────────────────────────

async def _cb_cancel(cq, context: CallbackContext, order_id: str) -> None:
    if cq.from_user.id != OWNER_ID:
        await cq.answer("❌ Owner only.", show_alert=True); return
    await cq.answer()

    order = await _find_order(order_id)
    if not order:
        await context.bot.send_message(OWNER_ID, "❌ Order မတွေ့ပါ။"); return
    if order.get("status") == "cancelled":
        await cq.answer("Already cancelled.", show_alert=True); return

    await mmshop_orders_collection.update_one(
        {"_id": order["_id"]},
        {"$set": {"status": "cancelled", "cancelled_at": time.time()}},
    )

    buyer_id  = order["buyer_id"]
    char_name = order.get("char_name", "Unknown")
    try:
        await context.bot.send_message(
            buyer_id,
            f"❌ <b>Order ပယ်ဖျက်ခံရပါသည်</b>\n\n"
            f"🌸 {escape(char_name)} — Owner က Cancel လုပ်လိုက်ပါသည်။\n"
            f"ငွေလွှဲမိပြီးဆိုရင် Owner ထံ တိုက်ရိုက် ဆက်သွယ်ပါ။",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    await cq.edit_message_reply_markup(InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancelled", callback_data="mm_noop"),
    ]]))
    await context.bot.send_message(OWNER_ID, "❌ Cancel ပြီး buyer ကို အကြောင်းကြားပြီးပါပြီ။")


# ── owner listings view ───────────────────────────────────────────────────────

async def _cb_owner_listings(cq, context: CallbackContext) -> None:
    listings = await mmshop_listings_collection.find({}).sort("listed_at", -1).to_list(50)
    if not listings:
        await context.bot.send_message(_cb_chat_id(cq), "📋 Listing မရှိသေး။")
        return
    lines = []
    for li in listings:
        sold   = li.get("sold_count", 0)
        copies = li.get("copies", 0)
        stock  = "∞" if copies == 0 else f"{sold}/{copies}"
        flag   = "✅" if not li.get("sold_out") else "❌"
        lines.append(
            f"{flag} <b>{escape(li.get('char_name','?'))}</b> — "
            f"{li.get('mmk_price',0):,} MMK — {stock} sold\n"
            f"   <code>/mmremove {li['_id']}</code>"
        )
    await context.bot.send_message(
        _cb_chat_id(cq),
        "📋 <b>MMK Shop Listings</b>\n\n" + "\n\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


def _cb_chat_id(cq) -> int:
    return cq.message.chat.id if cq.message else cq.from_user.id


# ── main callback router ──────────────────────────────────────────────────────

async def _mm_callback(update: Update, context: CallbackContext) -> None:
    cq   = update.callback_query
    data = cq.data or ""

    if data == "mm_noop":
        await cq.answer()
        return

    if data.startswith("mm_page_"):
        await cq.answer()
        page = int(data.split("_")[-1])
        await _send_page(update, context, page)

    elif data.startswith("mm_buy_"):
        listing_id = data[len("mm_buy_"):]
        await _cb_buy(cq, context, listing_id)       # answers internally

    elif data.startswith("mm_pay_"):
        parts      = data.split("_", 3)              # mm_pay_kpay_<id>
        pay_type   = parts[2]
        listing_id = parts[3]
        await _cb_pay(cq, context, pay_type, listing_id)  # answers internally

    elif data.startswith("mm_confirm_"):
        order_id = data[len("mm_confirm_"):]
        await _cb_confirm(cq, context, order_id)     # answers internally

    elif data.startswith("mm_cancel_"):
        order_id = data[len("mm_cancel_"):]
        await _cb_cancel(cq, context, order_id)      # answers internally

    elif data == "mm_owner_listings":
        await cq.answer()
        await _cb_owner_listings(cq, context)

    else:
        await cq.answer()


# ── MMK Group Drop System ─────────────────────────────────────────────────────

async def _check_group_qualify(bot, chat_id: int) -> bool:
    """Return True if the group meets the minimum requirements for MMK drops."""
    try:
        member_count = await bot.get_chat_member_count(chat_id)
        if member_count < _MM_MIN_MEMBERS:
            return False
    except Exception:
        return False

    now = time.time()
    users = _mm_unique_users.get(chat_id, {})
    # Count users who sent a message within the last hour
    active = sum(1 for ts in users.values() if now - ts <= 3600)
    return active >= _MM_MIN_ACTIVE


async def _mm_expire_drop(chat_id: int, msg, listing_id: str) -> None:
    """Edit the drop message to 'expired' after _MM_EXPIRE_SECS seconds."""
    await asyncio.sleep(_MM_EXPIRE_SECS)
    # Only expire if this listing is still the active drop
    active = _mm_active_drop.get(chat_id)
    if active and str(active.get("_id", "")) == listing_id:
        _mm_active_drop.pop(chat_id, None)
        expired_txt = (
            "⏰ <b>MMK Drop — ကုန်သွားပြီ!</b>\n\n"
            "Card မရပြီ — /mmshop မှာ ဝယ်ယူနိုင်သည်။"
        )
        try:
            await msg.edit_caption(expired_txt, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup([[
                                       InlineKeyboardButton("🏪 /mmshop", callback_data="mm_noop")
                                   ]]))
        except Exception:
            try:
                await msg.edit_text(expired_txt, parse_mode=ParseMode.HTML,
                                    reply_markup=InlineKeyboardMarkup([[
                                        InlineKeyboardButton("🏪 /mmshop", callback_data="mm_noop")
                                    ]]))
            except Exception:
                pass


async def _send_mm_drop(chat_id: int, bot) -> None:
    """Pick a random active MMK listing and post it as a group drop."""
    listings = await mmshop_listings_collection.find(
        {"sold_out": {"$ne": True}}
    ).to_list(200)

    if not listings:
        return

    # Weight by lower price first (better deals more likely to show)
    li = random.choice(listings)
    lid = str(li["_id"])

    _mm_active_drop[chat_id] = li
    _mm_last_drop[chat_id]   = time.time()

    copies = li.get("copies", 0)
    sold   = li.get("sold_count", 0)
    stock  = "♾️ Unlimited" if copies == 0 else f"{copies - sold} ကျန်"

    cap = (
        f"💰 <b>MMK Special Drop!</b>\n\n"
        f"🌸 <b>{escape(li.get('char_name', ''))}</b>\n"
        f"📺 {escape(li.get('char_anime', ''))}\n"
        f"💎 {escape(li.get('char_rarity', ''))}\n\n"
        f"💵 Price: <b>{li.get('mmk_price', 0):,} MMK</b>\n"
        f"📦 Stock: {stock}\n\n"
        f"⏰ {_MM_EXPIRE_SECS} seconds ထဲ DM to Buy!"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 To Buy (DM)", callback_data=f"mm_buy_{lid}"),
    ]])

    img = li.get("img_url", "")
    try:
        if li.get("media_type") == "video":
            msg = await bot.send_video(chat_id, img, caption=cap,
                                       parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            msg = await bot.send_photo(chat_id, img, caption=cap,
                                       parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        try:
            msg = await bot.send_message(chat_id, cap,
                                         parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            _mm_active_drop.pop(chat_id, None)
            return

    asyncio.create_task(_mm_expire_drop(chat_id, msg, lid))


async def _mm_message_counter(update: Update, context: CallbackContext) -> None:
    """Track messages per group and trigger MMK drops when threshold is hit."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private" or not user:
        return

    chat_id = chat.id
    user_id = user.id
    now     = time.time()

    # Track unique active users (rolling 1-hour window)
    bucket = _mm_unique_users.setdefault(chat_id, {})
    bucket[user_id] = now
    # Prune stale entries every ~500 messages to save memory
    cnt = _mm_msg_count.get(chat_id, 0)
    if cnt % 500 == 0 and bucket:
        cutoff = now - 3600
        _mm_unique_users[chat_id] = {u: t for u, t in bucket.items() if t > cutoff}

    # Count messages
    _mm_msg_count[chat_id] = cnt + 1

    if _mm_msg_count[chat_id] < _MM_MSG_THRESHOLD:
        return

    # Cooldown guard
    last = _mm_last_drop.get(chat_id, 0)
    if now - last < _MM_COOLDOWN_SECS:
        _mm_msg_count[chat_id] = 0
        return

    # Skip if drop already active
    if _mm_active_drop.get(chat_id):
        _mm_msg_count[chat_id] = 0
        return

    # Check group qualifications
    if not await _check_group_qualify(context.bot, chat_id):
        _mm_msg_count[chat_id] = 0
        return

    _mm_msg_count[chat_id] = 0
    asyncio.create_task(_send_mm_drop(chat_id, context.bot))


# ── /mmsetexpire — owner can change expire time ───────────────────────────────

async def mmsetexpire_cmd(update: Update, context: CallbackContext) -> None:
    global _MM_EXPIRE_SECS
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Owner only.")
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            f"Current expire: <b>{_MM_EXPIRE_SECS}s</b>\n"
            "Usage: <code>/mmsetexpire &lt;seconds&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    _MM_EXPIRE_SECS = max(10, int(args[0]))
    await bot_settings_collection.update_one(
        {"_id": "mm_expire_secs"}, {"$set": {"value": _MM_EXPIRE_SECS}}, upsert=True)
    await update.message.reply_text(
        f"✅ MMK drop expire: <b>{_MM_EXPIRE_SECS}s</b>", parse_mode=ParseMode.HTML)


# ── register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler("mmshop",      mmshop_cmd,      block=False))
application.add_handler(CommandHandler("mmremove",    mmremove_cmd,    block=False))
application.add_handler(CommandHandler("setphone",    setphone_cmd,    block=False))
application.add_handler(CommandHandler("mmsetexpire", mmsetexpire_cmd, block=False))
application.add_handler(CallbackQueryHandler(_mm_callback, pattern=r"^mm_", block=False))
application.add_handler(MessageHandler(
    filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL),
    _receipt_handler,
    block=False,
))
application.add_handler(MessageHandler(
    filters.ChatType.GROUPS & filters.TEXT & (~filters.COMMAND),
    _mm_message_counter,
    block=False,
))
