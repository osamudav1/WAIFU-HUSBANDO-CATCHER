"""
modules/trade.py — Trade & Gift, fully in PTB.
"""
import asyncio
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import KeyboardButtonStyle, ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection

_trades: dict[str, dict] = {}
_gifts:  dict[str, dict] = {}
_EXPIRY = 300


async def _expire(store: dict, key: str) -> None:
    await asyncio.sleep(_EXPIRY)
    store.pop(key, None)


# ── /trade ────────────────────────────────────────────────────────────────────

async def trade(update: Update, context: CallbackContext) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Reply to a user's message.\n"
            "Usage: <code>/trade YOUR_ID THEIR_ID</code>",
            parse_mode=ParseMode.HTML)
        return
    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: <code>/trade YOUR_CHAR_ID THEIR_CHAR_ID</code>",
            parse_mode=ParseMode.HTML)
        return

    a  = update.effective_user
    b  = update.message.reply_to_message.from_user
    if a.id == b.id:
        await update.message.reply_text("❌ Can't trade with yourself!")
        return

    a_doc = await user_collection.find_one({"id": a.id})
    b_doc = await user_collection.find_one({"id": b.id})

    a_char = next((c for c in (a_doc or {}).get("characters", []) if c["id"] == context.args[0]), None)
    b_char = next((c for c in (b_doc or {}).get("characters", []) if c["id"] == context.args[1]), None)

    if not a_char:
        await update.message.reply_text("❌ You don't own that character."); return
    if not b_char:
        await update.message.reply_text("❌ They don't own that character."); return

    tid = f"tr_{a.id}_{b.id}_{int(time.time())}"
    _trades[tid] = {
        "a_id": a.id, "a_name": a.first_name, "a_char": a_char,
        "b_id": b.id, "b_name": b.first_name, "b_char": b_char,
    }
    asyncio.create_task(_expire(_trades, tid))

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"trade_yes:{tid}", style=KeyboardButtonStyle.SUCCESS),
        InlineKeyboardButton("❌ Reject", callback_data=f"trade_no:{tid}", style=KeyboardButtonStyle.DANGER),
    ]])
    await update.message.reply_text(
        f"🔄 <b>Trade Proposal</b>\n\n"
        f"<a href='tg://user?id={a.id}'>{escape(a.first_name)}</a> offers "
        f"<b>{escape(a_char['name'])}</b>\n"
        f"for <b>{escape(b_char['name'])}</b> from "
        f"<a href='tg://user?id={b.id}'>{escape(b.first_name)}</a>\n\n"
        f"<i>{escape(b.first_name)}, accept?</i>",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


async def trade_cb(update: Update, context: CallbackContext) -> None:
    q      = update.callback_query
    action, tid = q.data.split(":", 1)

    state = _trades.pop(tid, None)
    if not state:
        await q.answer("⌛ Trade expired!", show_alert=True); return
    if q.from_user.id != state["b_id"]:
        await q.answer("❌ Not your trade!", show_alert=True); return

    await q.answer()
    if action == "trade_no":
        await q.edit_message_text("❌ Trade declined."); return

    a_id, b_id = state["a_id"], state["b_id"]
    a_char, b_char = state["a_char"], state["b_char"]

    await user_collection.update_one({"id": a_id}, {"$pull": {"characters": {"id": a_char["id"]}}})
    await user_collection.update_one({"id": b_id}, {"$pull": {"characters": {"id": b_char["id"]}}})
    await user_collection.update_one({"id": a_id}, {"$push": {"characters": b_char}})
    await user_collection.update_one({"id": b_id}, {"$push": {"characters": a_char}})

    await q.edit_message_text(
        f"✅ Trade complete!\n\n"
        f"<a href='tg://user?id={a_id}'>{escape(state['a_name'])}</a> ← <b>{escape(b_char['name'])}</b>\n"
        f"<a href='tg://user?id={b_id}'>{escape(state['b_name'])}</a> ← <b>{escape(a_char['name'])}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /gift ─────────────────────────────────────────────────────────────────────

async def gift(update: Update, context: CallbackContext) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Reply to a user. Usage: <code>/gift CHAR_ID</code>", parse_mode=ParseMode.HTML)
        return
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/gift CHAR_ID</code>", parse_mode=ParseMode.HTML)
        return

    a = update.effective_user
    b = update.message.reply_to_message.from_user
    if a.id == b.id:
        await update.message.reply_text("❌ Can't gift to yourself!"); return

    a_doc = await user_collection.find_one({"id": a.id})
    char  = next((c for c in (a_doc or {}).get("characters", []) if c["id"] == context.args[0]), None)
    if not char:
        await update.message.reply_text("❌ That character isn't in your collection."); return

    gid = f"gi_{a.id}_{b.id}_{int(time.time())}"
    _gifts[gid] = {
        "a_id": a.id, "a_name": a.first_name,
        "b_id": b.id, "b_name": b.first_name, "b_username": b.username,
        "char": char,
    }
    asyncio.create_task(_expire(_gifts, gid))

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎁 Confirm", callback_data=f"gift_yes:{gid}", style=KeyboardButtonStyle.SUCCESS),
        InlineKeyboardButton("❌ Cancel",  callback_data=f"gift_no:{gid}",  style=KeyboardButtonStyle.DANGER),
    ]])
    await update.message.reply_text(
        f"🎁 Gift <b>{escape(char['name'])}</b> to "
        f"<a href='tg://user?id={b.id}'>{escape(b.first_name)}</a>?",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


async def gift_cb(update: Update, context: CallbackContext) -> None:
    q      = update.callback_query
    action, gid = q.data.split(":", 1)

    state = _gifts.pop(gid, None)
    if not state:
        await q.answer("⌛ Gift expired!", show_alert=True); return
    if q.from_user.id != state["a_id"]:
        await q.answer("❌ Not your gift!", show_alert=True); return

    await q.answer()
    if action == "gift_no":
        await q.edit_message_text("❌ Gift cancelled."); return

    char = state["char"]
    await user_collection.update_one(
        {"id": state["a_id"]}, {"$pull": {"characters": {"id": char["id"]}}})

    b_doc = await user_collection.find_one({"id": state["b_id"]})
    if b_doc:
        await user_collection.update_one({"id": state["b_id"]}, {"$push": {"characters": char}})
    else:
        await user_collection.insert_one({
            "id": state["b_id"], "username": state["b_username"],
            "first_name": state["b_name"], "characters": [char],
            "coins": 0, "xp": 0, "wins": 0, "total_guesses": 0, "favorites": [],
        })

    await q.edit_message_text(
        f"✅ <b>{escape(char['name'])}</b> gifted to "
        f"<a href='tg://user?id={state['b_id']}'>{escape(state['b_name'])}</a>!",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("trade", trade, block=False))
application.add_handler(CommandHandler("gift",  gift,  block=False))
application.add_handler(CallbackQueryHandler(trade_cb, pattern=r"^trade_(yes|no):", block=False))
application.add_handler(CallbackQueryHandler(gift_cb,  pattern=r"^gift_(yes|no):",  block=False))
