"""
modules/starshop.py — Owner-only Star / TON marketplace.

Owner commands (DM only):
  /star  <char_id> <stars> [ton]   list a character for sale
  /delstar <listing_id>            remove a listing
  /starlist                        view all live listings
  /setton <wallet>                 set / change owner TON wallet (also via env OWNER_TON_WALLET)

Public commands:
  /starshop                        browse the star-shop (paginated cards)

Buyer flow (per card):
  ⭐  Buy: N Stars       Telegram invoice (currency=XTR) → auto-deliver
  💎  Buy: X TON         deeplink + memo → user pays → "Verify" → auto-deliver

Notes
-----
* Stars go to the bot owner's Stars balance automatically (Telegram side).
* TON payments go directly wallet-to-wallet. The bot only verifies payment
  via the public Toncenter API (no API key needed for light usage).
"""
from __future__ import annotations

import math
import os
import secrets
import time
from html import escape

import httpx
from bson import ObjectId
from pymongo import ReturnDocument

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
)
from telegram.constants import ChatType, KeyboardButtonStyle, ParseMode
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from waifu import (
    LOGGER,
    OWNER_ID,
    application,
    collection,
    star_market_collection,
    ton_orders_collection,
    user_collection,
    sudo_users,
    bot_settings_collection,
)
from waifu.cache import db_op, invalidate_user

# ── Constants ─────────────────────────────────────────────────────────────────

_PAGE_SIZE        = 6
_TON_API          = "https://toncenter.com/api/v2/getTransactions"
_TON_DECIMALS     = 1_000_000_000        # 1 TON = 1e9 nano
_TON_TX_TOLERANCE = 0.001                # ±0.001 TON acceptance
_DEFAULT_RATE     = 91                   # 91 stars = 1 TON  →  50 stars ≈ 0.55 TON (owner can change /setrate)

OWNER_TON_WALLET_ENV = os.environ.get("OWNER_TON_WALLET", "").strip()


# ── Settings (DB-backed) ──────────────────────────────────────────────────────

async def _get_stars_per_ton() -> int:
    doc = await bot_settings_collection.find_one({"_id": "stars_per_ton"})
    if doc and isinstance(doc.get("value"), (int, float)) and doc["value"] > 0:
        return int(doc["value"])
    return _DEFAULT_RATE


async def _set_stars_per_ton(rate: int) -> None:
    await bot_settings_collection.find_one_and_update(
        {"_id": "stars_per_ton"},
        {"$set": {"value": int(rate)}},
        upsert=True,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_owner(uid: int) -> bool:
    return uid == OWNER_ID or uid in sudo_users


def _dm_only(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == ChatType.PRIVATE)


async def _get_ton_wallet() -> str:
    """Return owner TON wallet (DB override > env var)."""
    doc = await bot_settings_collection.find_one({"_id": "owner_ton_wallet"})
    if doc and doc.get("value"):
        return str(doc["value"]).strip()
    return OWNER_TON_WALLET_ENV


async def _set_ton_wallet(addr: str) -> None:
    await bot_settings_collection.find_one_and_update(
        {"_id": "owner_ton_wallet"},
        {"$set": {"value": addr.strip()}},
        upsert=True,
    )


def _fmt_listing_caption(li: dict) -> str:
    c = li.get("char", {})
    parts = [
        f"<b>{escape(c.get('name','?'))}</b>",
        f"<i>{escape(c.get('anime','?'))}</i>",
        f"Rarity: {escape(c.get('rarity','?'))}",
        f"Char ID: <code>{escape(str(c.get('id','?')))}</code>",
        f"Listing ID: <code>{li['_id']}</code>",
        "",
    ]
    if li.get("star_price"):
        parts.append(f"⭐ <b>{li['star_price']}</b> Stars")
    if li.get("ton_price"):
        parts.append(f"💎 <b>{li['ton_price']:g}</b> TON")
    return "\n".join(parts)


def _cb_chat_id(cq) -> int:
    """Return a chat_id we can DM/send to even if cq came from inline message."""
    if cq.message:
        return cq.message.chat_id
    return cq.from_user.id


async def _cb_send(cq, context, text: str, **kwargs) -> None:
    """Reply to a callback_query whether it has a message context or is inline."""
    if cq.message:
        await cq.message.reply_text(text, **kwargs)
    else:
        await context.bot.send_message(cq.from_user.id, text, **kwargs)


def _buy_keyboard(li: dict, ton_enabled: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if li.get("star_price"):
        rows.append([InlineKeyboardButton(
            f"⭐  Buy: {li['star_price']} Stars",
            callback_data=f"sshop_buystar_{li['_id']}",
            style=KeyboardButtonStyle.SUCCESS,
        )])
    if ton_enabled and li.get("ton_price"):
        rows.append([InlineKeyboardButton(
            f"💎  Buy: {li['ton_price']:g} TON",
            callback_data=f"sshop_buyton_{li['_id']}",
            style=KeyboardButtonStyle.PRIMARY,
        )])
    rows.append([InlineKeyboardButton(
        "⬅️ Back", callback_data="sshop_page_0",
        style=KeyboardButtonStyle.DANGER,
    )])
    return InlineKeyboardMarkup(rows)


# ── Owner Panel ───────────────────────────────────────────────────────────────

async def _show_owner_panel(update: Update) -> None:
    wallet = await _get_ton_wallet()
    rate   = await _get_stars_per_ton()
    total  = await star_market_collection.count_documents({})

    text = (
        "👑 <b>Star-Shop Owner Panel</b>\n\n"
        f"💎 TON Wallet: <code>{escape(wallet) if wallet else '— မချိတ်ရသေး —'}</code>\n"
        f"⚙️ Conversion: <b>{rate}</b> ⭐ = 1 💎 TON\n"
        f"📋 Live Listings: <b>{total}</b>\n\n"
        "<b>Commands:</b>\n"
        "<code>/star &lt;char_id&gt; &lt;stars&gt; [ton]</code>  — list a character\n"
        "<code>/delstar &lt;listing_id&gt;</code>  — remove\n"
        "<code>/starlist</code>  — view all listings\n"
        "<code>/setton &lt;wallet&gt;</code>  — set TON wallet\n"
        "<code>/setrate &lt;stars_per_ton&gt;</code>  — change conversion rate\n"
    )
    rows: list[list[InlineKeyboardButton]] = []
    if not wallet:
        rows.append([InlineKeyboardButton(
            "🔗  Connect TON Wallet",
            callback_data="sshop_owner_connect",
            style=KeyboardButtonStyle.PRIMARY,
        )])
    else:
        rows.append([InlineKeyboardButton(
            "🔄  Change TON Wallet",
            callback_data="sshop_owner_connect",
            style=KeyboardButtonStyle.PRIMARY,
        )])
    rows.append([
        InlineKeyboardButton("📋 Listings", callback_data="sshop_owner_listings",
                             style=KeyboardButtonStyle.SUCCESS),
        InlineKeyboardButton("🛒 Open Shop", callback_data="sshop_page_0",
                             style=KeyboardButtonStyle.PRIMARY),
    ])
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


# ── /star — owner adds a listing ──────────────────────────────────────────────

async def star_cmd(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    if not _is_owner(uid):
        return
    if not _dm_only(update):
        await update.message.reply_text("⚠️ Owner DM ထဲမှာသာ သုံးပါ။")
        return

    args = context.args or []

    # No args → show owner welcome panel
    if not args:
        await _show_owner_panel(update)
        return

    if len(args) < 2:
        await update.message.reply_text(
            "Usage:\n<code>/star &lt;char_id&gt; &lt;star_amount&gt; [ton_amount]</code>\n\n"
            "Example:\n<code>/star 42 50</code>  →  50 Stars (TON auto-calculated)\n"
            "<code>/star 42 50 0.5</code>  →  50 Stars or 0.5 TON (manual)",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id_raw = args[0].lstrip("0") or args[0]
    try:
        star_price = int(args[1])
        ton_price  = float(args[2]) if len(args) >= 3 else None     # None = auto
    except ValueError:
        await update.message.reply_text("❌ Star/TON amount သည် နံပါတ် ဖြစ်ရမည်။")
        return

    if star_price < 1:
        await update.message.reply_text("❌ Star amount ≥ 1 ဖြစ်ရမည်။")
        return

    # Auto-calc TON from Stars if not provided
    if ton_price is None:
        rate = await _get_stars_per_ton()
        ton_price = round(star_price / rate, 4)

    if ton_price < 0:
        await update.message.reply_text("❌ TON amount ≥ 0 ဖြစ်ရမည်။")
        return

    char = await collection.find_one({"id": char_id_raw})
    if not char:
        await update.message.reply_text(f"❌ Char ID <code>{escape(char_id_raw)}</code> ကို မတွေ့ပါ။", parse_mode=ParseMode.HTML)
        return

    char.pop("_id", None)
    listing = {
        "char_id":    char_id_raw,
        "char":       char,
        "star_price": star_price,
        "ton_price":  ton_price if ton_price > 0 else None,
        "ton_nano":   int(round(ton_price * _TON_DECIMALS)) if ton_price > 0 else None,
        "listed_at":  time.time(),
        "listed_by":  uid,
    }
    res = await star_market_collection.insert_one(listing)
    listing["_id"] = res.inserted_id

    cap = (
        "✅ <b>Star-Shop တွင် တင်ပြီးပြီ!</b>\n\n"
        f"{_fmt_listing_caption(listing)}\n\n"
        "User တွေ <code>/starshop</code> နဲ့ ဝယ်နိုင်ပြီ။"
    )
    img = char.get("img_url") or char.get("image_url")
    try:
        if img:
            await update.message.reply_photo(img, caption=cap, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(cap, parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(cap, parse_mode=ParseMode.HTML)


# ── /delstar — owner removes ──────────────────────────────────────────────────

async def delstar_cmd(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    if not _is_owner(uid):
        return
    if not _dm_only(update):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: <code>/delstar &lt;listing_id&gt;</code>", parse_mode=ParseMode.HTML)
        return
    try:
        oid = ObjectId(args[0])
    except Exception:
        await update.message.reply_text("❌ Listing ID မှားနေသည်။")
        return
    res = await star_market_collection.delete_one({"_id": oid})
    if res.deleted_count:
        await update.message.reply_text("🗑️ Listing ဖျက်ပြီးပြီ။")
    else:
        await update.message.reply_text("❌ Listing မတွေ့ပါ။")


# ── /starlist — owner views all listings ──────────────────────────────────────

async def starlist_cmd(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    if not _is_owner(uid):
        return
    if not _dm_only(update):
        return
    items = await star_market_collection.find({}).sort("listed_at", -1).to_list(50)
    if not items:
        await update.message.reply_text("📭 Star-Shop ဗလာ။")
        return
    lines = [f"📋 <b>Star-Shop Listings ({len(items)})</b>\n"]
    for li in items:
        c = li["char"]
        bits = []
        if li.get("star_price"): bits.append(f"⭐{li['star_price']}")
        if li.get("ton_price"):  bits.append(f"💎{li['ton_price']:g}")
        lines.append(
            f"• <code>{li['_id']}</code> — {escape(c.get('name','?'))} "
            f"({escape(c.get('rarity','?'))}) — {' / '.join(bits) or 'no price'}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /setton — owner sets TON wallet ───────────────────────────────────────────

async def setton_cmd(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    if not _is_owner(uid):
        return
    if not _dm_only(update):
        return
    args = context.args or []
    if not args:
        cur = await _get_ton_wallet()
        await update.message.reply_text(
            f"Current TON wallet: <code>{escape(cur or '— မထားရသေးပါ —')}</code>\n\n"
            "Usage: <code>/setton &lt;wallet_address&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    addr = args[0].strip()
    if len(addr) < 40 or " " in addr:
        await update.message.reply_text("❌ TON wallet address မှားနေပုံပေါ်သည်။")
        return
    await _set_ton_wallet(addr)
    await update.message.reply_text(f"✅ Owner TON wallet ပြောင်းပြီးပြီ:\n<code>{escape(addr)}</code>", parse_mode=ParseMode.HTML)


# ── /starshop — public browse ─────────────────────────────────────────────────

async def starshop_cmd(update: Update, context: CallbackContext) -> None:
    await _send_page(update, context, page=0, edit=False)


async def _send_page(update: Update, context: CallbackContext, page: int, edit: bool) -> None:
    total = await star_market_collection.count_documents({})

    # Single "Star Characters" inline_query button — opens card gallery
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛒  Star Characters",
            switch_inline_query_current_chat="starshop",
            style=KeyboardButtonStyle.PRIMARY,
        ),
    ]])

    if total == 0:
        text = (
            "🛒 <b>Star-Shop</b>\n\n"
            "<i>Owner က listing မထည့်ရသေးပါ။</i>"
        )
    else:
        text = (
            f"🛒 <b>Star-Shop</b>  •  {total} listing{'s' if total != 1 else ''}\n\n"
            "<i>အောက်က button ကို နှိပ်ပြီး character cards ကို ကြည့်ပါ</i>"
        )

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb,
            )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb,
        )


# ── Callback router ───────────────────────────────────────────────────────────

async def starshop_cb(update: Update, context: CallbackContext) -> None:
    cq   = update.callback_query
    data = cq.data or ""
    await cq.answer()

    if data == "sshop_noop":
        return

    if data == "sshop_owner_connect":
        if not _is_owner(cq.from_user.id):
            return
        await cq.message.reply_text(
            "🔗 <b>Connect TON Wallet</b>\n\n"
            "မင်းရဲ့ TON wallet address ကို အောက်ပါ command နဲ့ ပို့ပါ:\n\n"
            "<code>/setton EQAbcd...xyz</code>\n\n"
            "<i>Tonkeeper / @wallet စသည့် TON wallet app ထဲက address ကို copy ထည့်ပါ။</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "sshop_owner_listings":
        if not _is_owner(cq.from_user.id):
            return
        items = await star_market_collection.find({}).sort("listed_at", -1).to_list(50)
        if not items:
            await cq.message.reply_text("📭 Star-Shop ဗလာ။")
            return
        lines = [f"📋 <b>Listings ({len(items)})</b>\n"]
        for li in items:
            c = li["char"]
            bits = []
            if li.get("star_price"): bits.append(f"⭐{li['star_price']}")
            if li.get("ton_price"):  bits.append(f"💎{li['ton_price']:g}")
            lines.append(
                f"• <code>{li['_id']}</code> — {escape(c.get('name','?'))} "
                f"({escape(c.get('rarity','?'))}) — {' / '.join(bits)}"
            )
        await cq.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    if data.startswith("sshop_page_"):
        try:
            page = int(data.rsplit("_", 1)[-1])
        except ValueError:
            return
        await _send_page(update, context, page=page, edit=True)
        return

    if data.startswith("sshop_view_"):
        oid_str = data.rsplit("_", 1)[-1]
        try:
            oid = ObjectId(oid_str)
        except Exception:
            return
        li = await star_market_collection.find_one({"_id": oid})
        if not li:
            await cq.edit_message_text("❌ Listing မတွေ့တော့ပါ။")
            return
        ton_wallet = await _get_ton_wallet()
        kb         = _buy_keyboard(li, ton_enabled=bool(ton_wallet))
        cap        = _fmt_listing_caption(li)
        img        = li["char"].get("img_url") or li["char"].get("image_url")
        try:
            if img:
                await cq.message.reply_photo(img, caption=cap, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await cq.message.reply_text(cap, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            await cq.message.reply_text(cap, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("sshop_buystar_"):
        await _start_star_payment(update, context, data.rsplit("_", 1)[-1])
        return

    if data.startswith("sshop_buyton_"):
        await _start_ton_payment(update, context, data.rsplit("_", 1)[-1])
        return

    if data.startswith("sshop_verifyton_"):
        await _verify_ton_payment(update, context, data.rsplit("_", 1)[-1])
        return


# ── Star (XTR) payment flow ───────────────────────────────────────────────────

async def _start_star_payment(update: Update, context: CallbackContext, oid_str: str) -> None:
    cq = update.callback_query
    try:
        oid = ObjectId(oid_str)
    except Exception:
        return
    li = await star_market_collection.find_one({"_id": oid})
    if not li or not li.get("star_price"):
        await cq.answer("❌ Listing မရှိတော့ပါ။", show_alert=True)
        return

    char  = li["char"]
    title = f"{char.get('name','Character')} ({char.get('rarity','?')})"
    desc  = f"{char.get('anime','?')} — Buy with Telegram Stars"
    payload = f"sshop:{oid}:{cq.from_user.id}"

    await context.bot.send_invoice(
        chat_id=_cb_chat_id(cq),
        title=title[:32],
        description=desc[:255],
        payload=payload,
        provider_token="",                     # Stars: empty
        currency="XTR",
        prices=[LabeledPrice(label=title[:32], amount=int(li["star_price"]))],
    )


async def precheckout_cb(update: Update, context: CallbackContext) -> None:
    pcq = update.pre_checkout_query
    if not pcq.invoice_payload.startswith("sshop:"):
        return                                  # not ours
    try:
        _, oid_str, uid_str = pcq.invoice_payload.split(":")
        ObjectId(oid_str)
        int(uid_str)
        await pcq.answer(ok=True)
    except Exception as e:
        await pcq.answer(ok=False, error_message=f"Invalid order: {e}")


async def successful_payment_cb(update: Update, context: CallbackContext) -> None:
    sp = update.message.successful_payment
    if not sp or not sp.invoice_payload.startswith("sshop:"):
        return
    try:
        _, oid_str, uid_str = sp.invoice_payload.split(":")
        oid = ObjectId(oid_str)
        uid = int(uid_str)
    except Exception:
        return

    # ── Atomic claim: only one concurrent callback can win ───────────────────
    li = await star_market_collection.find_one_and_delete({"_id": oid})
    if not li:
        # Already sold — Stars charged but no character to deliver. Notify owner for refund.
        await update.message.reply_text(
            "⚠️ Listing သည် ရောင်းချပြီးသွားပြီ — Owner က Star ပြန်အမ်းပေးရန် အကြောင်းကြားသွားမည်။"
        )
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"⚠️ <b>Refund needed</b>\n"
                f"Buyer: <code>{uid}</code> paid {sp.total_amount} XTR\n"
                f"Listing <code>{oid}</code> already sold.\n"
                f"Charge ID: <code>{sp.telegram_payment_charge_id}</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    char = li["char"]
    await user_collection.update_one(
        {"id": uid},
        {"$push": {"characters": char}, "$setOnInsert": {
            "id": uid, "username": update.effective_user.username,
            "first_name": update.effective_user.first_name,
            "coins": 0, "xp": 0, "wins": 0, "total_guesses": 0, "favorites": [],
        }},
        upsert=True,
    )
    invalidate_user(uid)

    await update.message.reply_text(
        f"🎉 <b>Payment Successful!</b>\n\n"
        f"<b>{escape(char.get('name','?'))}</b> ({escape(char.get('rarity','?'))}) "
        f"ကို <code>/harem</code> တွင် တွေ့နိုင်ပြီ။",
        parse_mode=ParseMode.HTML,
    )

    # Notify owner
    try:
        await context.bot.send_message(
            OWNER_ID,
            f"⭐ Star sale: <b>{li['star_price']}</b> XTR\n"
            f"Buyer: <code>{uid}</code>\n"
            f"Char: {escape(char.get('name','?'))} (id {escape(str(char.get('id','?')))})",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ── TON payment flow ──────────────────────────────────────────────────────────

async def _start_ton_payment(update: Update, context: CallbackContext, oid_str: str) -> None:
    cq = update.callback_query
    wallet = await _get_ton_wallet()
    if not wallet:
        await cq.answer("⚠️ Owner က TON wallet မချိတ်ရသေး။", show_alert=True)
        return

    try:
        oid = ObjectId(oid_str)
    except Exception:
        return
    li = await star_market_collection.find_one({"_id": oid})
    if not li:
        await cq.answer("❌ Listing မရှိ။", show_alert=True)
        return

    # Auto-derive TON for legacy listings missing ton_price
    ton_price = li.get("ton_price")
    ton_nano  = li.get("ton_nano")
    if (not ton_price or not ton_nano) and li.get("star_price"):
        rate = await _get_stars_per_ton()
        ton_price = round(li["star_price"] / rate, 4)
        ton_nano  = int(round(ton_price * _TON_DECIMALS))

    if not ton_price or not ton_nano:
        await cq.answer("❌ TON price မရရှိနိုင်ပါ။", show_alert=True)
        return

    memo = "WMK-" + secrets.token_hex(4).upper()
    order = {
        "listing_id": oid,
        "buyer_id":   cq.from_user.id,
        "amount_nano": ton_nano,
        "wallet":     wallet,
        "memo":       memo,
        "created_at": time.time(),
        "status":     "pending",
    }
    res = await ton_orders_collection.insert_one(order)
    order_id = str(res.inserted_id)

    deeplink = f"ton://transfer/{wallet}?amount={ton_nano}&text={memo}"
    text = (
        "💎 <b>TON Payment</b>\n\n"
        f"Amount: <b>{ton_price:g} TON</b>\n"
        f"To wallet: <code>{escape(wallet)}</code>\n"
        f"Memo (REQUIRED): <code>{memo}</code>\n\n"
        "1. Tap <b>Open Wallet</b> below (Tonkeeper / @wallet)\n"
        "2. Send the exact amount with the memo\n"
        "3. Tap <b>Verify Payment</b> after sending\n\n"
        "<i>Memo မထည့်ရင် auto-deliver မဖြစ်ပါ — Owner ထံ ဆက်သွယ်ပါ။</i>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 Open Wallet", url=deeplink, style=KeyboardButtonStyle.PRIMARY)],
        [InlineKeyboardButton("✅ Verify Payment", callback_data=f"sshop_verifyton_{order_id}", style=KeyboardButtonStyle.SUCCESS)],
    ])
    await context.bot.send_message(
        _cb_chat_id(cq), text, parse_mode=ParseMode.HTML, reply_markup=kb,
    )


async def _verify_ton_payment(update: Update, context: CallbackContext, order_id: str) -> None:
    cq = update.callback_query
    try:
        oid = ObjectId(order_id)
    except Exception:
        return
    order = await ton_orders_collection.find_one({"_id": oid})
    if not order:
        await cq.answer("❌ Order မတွေ့ပါ။", show_alert=True)
        return
    if order["status"] == "paid":
        await cq.answer("✅ ပေးပြီးသား — character ပို့ပြီးပြီ။", show_alert=True)
        return
    if order["buyer_id"] != cq.from_user.id:
        await cq.answer("❌ ဒီ order က မင်းရဲ့ မဟုတ်ပါ။", show_alert=True)
        return

    # ── Atomic claim of order: only one concurrent verify wins ─────────────
    claimed = await ton_orders_collection.find_one_and_update(
        {"_id": oid, "status": "pending"},
        {"$set": {"status": "verifying", "verifying_at": time.time()}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        await cq.answer("⏳ Verifying ပြီးနေပြီ — ခဏစောင့်ပါ။", show_alert=True)
        return
    order = claimed

    # Helper to revert claim on failure/retry
    async def _release() -> None:
        await ton_orders_collection.update_one(
            {"_id": oid, "status": "verifying"},
            {"$set": {"status": "pending"}},
        )

    # Query Toncenter
    await cq.answer("🔍 Verifying…")
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get(_TON_API, params={"address": order["wallet"], "limit": 25})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        LOGGER.warning("Toncenter error: %s", e)
        await _release()
        await _cb_send(cq, context, "⚠️ TON network query မအောင်မြင်ပါ — ခဏကြာပြီး ပြန်စမ်းပါ။")
        return

    if not data.get("ok"):
        await _release()
        await _cb_send(cq, context, "⚠️ TON API error — ခဏကြာပြီး ပြန်စမ်းပါ။")
        return

    found_tx = None
    expected = order["amount_nano"]
    tol      = int(_TON_TX_TOLERANCE * _TON_DECIMALS)
    for tx in data.get("result", []):
        in_msg = tx.get("in_msg") or {}
        msg_text = (in_msg.get("message") or "").strip()
        if order["memo"] not in msg_text:
            continue
        try:
            value_nano = int(in_msg.get("value", 0))
        except Exception:
            continue
        if abs(value_nano - expected) > tol:
            continue
        found_tx = tx
        break

    if not found_tx:
        await _release()
        await _cb_send(cq, context,
            "⏳ Payment မတွေ့သေးပါ။\n\n"
            "TON network confirm ဖို့ ၁-၂ မိနစ် ကြာတတ်ပါသည်။\n"
            "ခဏကြာပြီး <b>Verify Payment</b> ပြန်နှိပ်ပါ။",
            parse_mode=ParseMode.HTML,
        )
        return

    # Atomically claim listing — if already sold via Stars / another TON order, mark stale
    li = await star_market_collection.find_one_and_delete({"_id": order["listing_id"]})
    if not li:
        await ton_orders_collection.update_one(
            {"_id": oid},
            {"$set": {"status": "stale_paid", "tx_hash": (found_tx.get("transaction_id") or {}).get("hash")}},
        )
        await _cb_send(cq, context,
            "⚠️ Listing သည် ရောင်းပြီးသွားပြီ — Owner ထံ TON refund အကြောင်း အကြောင်းကြားပြီ။"
        )
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"⚠️ <b>TON Refund needed</b>\n"
                f"Buyer: <code>{order['buyer_id']}</code> paid {order['amount_nano']/_TON_DECIMALS:g} TON\n"
                f"Memo: <code>{order['memo']}</code>\n"
                f"Listing already gone.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    char = li["char"]
    uid  = order["buyer_id"]
    await user_collection.update_one(
        {"id": uid},
        {"$push": {"characters": char}, "$setOnInsert": {
            "id": uid, "username": cq.from_user.username,
            "first_name": cq.from_user.first_name,
            "coins": 0, "xp": 0, "wins": 0, "total_guesses": 0, "favorites": [],
        }},
        upsert=True,
    )
    invalidate_user(uid)

    await ton_orders_collection.update_one(
        {"_id": oid},
        {"$set": {"status": "paid", "tx_hash": found_tx.get("transaction_id", {}).get("hash")}},
    )

    await _cb_send(cq, context,
        f"🎉 <b>TON Payment Verified!</b>\n\n"
        f"<b>{escape(char.get('name','?'))}</b> ({escape(char.get('rarity','?'))}) "
        f"ကို <code>/harem</code> တွင် တွေ့နိုင်ပြီ။",
        parse_mode=ParseMode.HTML,
    )
    try:
        await context.bot.send_message(
            OWNER_ID,
            f"💎 TON sale: <b>{li['ton_price']:g}</b> TON\n"
            f"Buyer: <code>{uid}</code>\n"
            f"Char: {escape(char.get('name','?'))} (id {escape(str(char.get('id','?')))})\n"
            f"Memo: <code>{order['memo']}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ── Handler registration ──────────────────────────────────────────────────────

async def setrate_cmd(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    if not _is_owner(uid) or not _dm_only(update):
        return
    args = context.args or []
    if not args:
        cur = await _get_stars_per_ton()
        await update.message.reply_text(
            f"⚙️ Current rate: <b>{cur}</b> ⭐ = 1 💎 TON\n\n"
            "Change with: <code>/setrate &lt;stars_per_ton&gt;</code>\n"
            "Example: <code>/setrate 500</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        rate = int(args[0])
        if rate < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Rate သည် positive integer ဖြစ်ရမည်။")
        return
    await _set_stars_per_ton(rate)
    await update.message.reply_text(
        f"✅ Conversion rate ပြောင်းပြီးပြီ: <b>{rate}</b> ⭐ = 1 💎 TON",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("star",      star_cmd,     block=False))
application.add_handler(CommandHandler("delstar",   delstar_cmd,  block=False))
application.add_handler(CommandHandler("starlist",  starlist_cmd, block=False))
application.add_handler(CommandHandler("setton",    setton_cmd,   block=False))
application.add_handler(CommandHandler("setrate",   setrate_cmd,  block=False))
application.add_handler(CommandHandler("starshop",  starshop_cmd, block=False))

application.add_handler(CallbackQueryHandler(starshop_cb,    pattern=r"^sshop_", block=False))
application.add_handler(PreCheckoutQueryHandler(precheckout_cb, block=False))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_cb, block=False))
