from pymongo import ReturnDocument
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, CallbackContext
from waifu import application, user_totals_collection, sudo_users, OWNER_ID
from waifu.config import Config

_MIN, _MAX = 3, 10_000


async def _is_admin(update: Update, context: CallbackContext) -> bool:
    uid = update.effective_user.id
    if uid == OWNER_ID or uid in sudo_users:
        return True
    m = await context.bot.get_chat_member(update.effective_chat.id, uid)
    return m.status in ("administrator", "creator")


async def get_freq(chat_id: int) -> int:
    doc = await user_totals_collection.find_one({"chat_id": chat_id})
    return int(doc["message_frequency"]) if doc and "message_frequency" in doc \
        else Config.DEFAULT_MSG_FREQUENCY


async def changemessage(update: Update, context: CallbackContext) -> None:
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only.")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        cur = await get_freq(update.effective_chat.id)
        await update.message.reply_text(
            f"📋  လောကြိုက်: <b>{cur}</b> message တိုင်း drop\n"
            f"သုံးပုံ: /changemessage &lt;{_MIN}–{_MAX}&gt;\n"
            f"ပြန်reset: /resetmessage",
            parse_mode=ParseMode.HTML)
        return
    n = int(context.args[0])
    if n < _MIN:
        await update.message.reply_text(f"❌ အနည်းဆုံး {_MIN}.")
        return
    if n > _MAX:
        await update.message.reply_text(f"❌ အများဆုံး {_MAX}.")
        return
    old = await get_freq(update.effective_chat.id)
    await user_totals_collection.find_one_and_update(
        {"chat_id": update.effective_chat.id},
        {"$set": {"message_frequency": n}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    await update.message.reply_text(
        f"✅ Drop frequency: <b>{old}</b> → <b>{n}</b> messages တိုင်း drop",
        parse_mode=ParseMode.HTML)


async def resetmessage(update: Update, context: CallbackContext) -> None:
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only.")
        return
    await user_totals_collection.update_one(
        {"chat_id": update.effective_chat.id},
        {"$unset": {"message_frequency": ""}},
    )
    await update.message.reply_text(
        f"✅ Default ပြန်သတ်မှတ်ပြီ: message <b>{Config.DEFAULT_MSG_FREQUENCY}</b> ခုတိုင်း drop",
        parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler(["changemessage", "changetime"], changemessage))
application.add_handler(CommandHandler(["resetmessage",  "resettime"],  resetmessage))
