from pymongo import ReturnDocument
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, CallbackContext
from waifu import application, user_totals_collection
from waifu.config import Config

_MIN, _MAX = 30, 10_000


async def _is_admin(update: Update, context: CallbackContext) -> bool:
    m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    return m.status in ("administrator", "creator")


async def get_freq(chat_id: int) -> int:
    doc = await user_totals_collection.find_one({"chat_id": chat_id})
    return int(doc["message_frequency"]) if doc and "message_frequency" in doc else Config.DEFAULT_MSG_FREQUENCY


async def changetime(update: Update, context: CallbackContext) -> None:
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only."); return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        cur = await get_freq(update.effective_chat.id)
        await update.message.reply_text(
            f"📋 Current: every <b>{cur}</b> messages\n"
            f"Usage: /changetime <{_MIN}–{_MAX}>\n"
            f"Reset: /resettime",
            parse_mode=ParseMode.HTML); return
    n = int(context.args[0])
    if n < _MIN:
        await update.message.reply_text(f"❌ Minimum {_MIN}."); return
    if n > _MAX:
        await update.message.reply_text(f"❌ Maximum {_MAX}."); return
    old = await get_freq(update.effective_chat.id)
    await user_totals_collection.find_one_and_update(
        {"chat_id": update.effective_chat.id},
        {"$set": {"message_frequency": n}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    await update.message.reply_text(
        f"✅ Drop frequency: <b>{old}</b> → <b>{n}</b> messages",
        parse_mode=ParseMode.HTML)


async def resettime(update: Update, context: CallbackContext) -> None:
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only."); return
    await user_totals_collection.update_one(
        {"chat_id": update.effective_chat.id},
        {"$unset": {"message_frequency": ""}},
    )
    await update.message.reply_text(
        f"✅ Reset to default: every <b>{Config.DEFAULT_MSG_FREQUENCY}</b> messages.",
        parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("changetime", changetime))
application.add_handler(CommandHandler("resettime",  resettime))
