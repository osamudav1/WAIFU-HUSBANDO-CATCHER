"""
modules/changetime.py — /changetime <minutes> per-group drop interval.

Admin-only. Sets how many minutes between each character drop.
"""
from pymongo import ReturnDocument
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, CallbackContext

from waifu import application, user_totals_collection, sudo_users, OWNER_ID
from waifu.config import Config

_MIN, _MAX = 1, 1_440   # 1 minute … 24 hours


async def _is_admin(update: Update, context: CallbackContext) -> bool:
    uid = update.effective_user.id
    if uid == OWNER_ID or uid in sudo_users:
        return True
    m = await context.bot.get_chat_member(update.effective_chat.id, uid)
    return m.status in ("administrator", "creator")


async def get_interval(chat_id: int) -> int:
    """Return drop interval in minutes for chat_id (DB value or default)."""
    doc = await user_totals_collection.find_one({"chat_id": chat_id})
    if doc and "drop_interval_minutes" in doc:
        return int(doc["drop_interval_minutes"])
    return Config.DROP_INTERVAL_MIN


async def changetime(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ Group ထဲမှာသာ သုံးပါ။")
        return
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        cur = await get_interval(update.effective_chat.id)
        await update.message.reply_text(
            f"⏱ လောကြိုက်: <b>{cur}</b> မိနစ်တိုင်း drop\n"
            f"သုံးပုံ: /changetime &lt;{_MIN}–{_MAX}&gt;\n"
            f"ပြန်reset: /resettime",
            parse_mode=ParseMode.HTML,
        )
        return

    n = int(context.args[0])
    if n < _MIN:
        await update.message.reply_text(f"❌ အနည်းဆုံး {_MIN} မိနစ်.")
        return
    if n > _MAX:
        await update.message.reply_text(f"❌ အများဆုံး {_MAX} မိနစ် (24 နာရီ).")
        return

    old = await get_interval(update.effective_chat.id)
    await user_totals_collection.find_one_and_update(
        {"chat_id": update.effective_chat.id},
        {"$set": {"drop_interval_minutes": n}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    await update.message.reply_text(
        f"✅ Drop interval: <b>{old}</b> → <b>{n}</b> မိနစ်တိုင်း drop\n"
        f"<i>(နောက် cycle မှ စတင် apply ဖြစ်မည်)</i>",
        parse_mode=ParseMode.HTML,
    )


async def resettime(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ Group ထဲမှာသာ သုံးပါ။")
        return
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only.")
        return

    await user_totals_collection.update_one(
        {"chat_id": update.effective_chat.id},
        {"$unset": {"drop_interval_minutes": ""}},
    )
    await update.message.reply_text(
        f"✅ Default ပြန်သတ်မှတ်ပြီ: <b>{Config.DROP_INTERVAL_MIN}</b> မိနစ်တိုင်း drop",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler(["changetime"], changetime))
application.add_handler(CommandHandler(["resettime"], resettime))
