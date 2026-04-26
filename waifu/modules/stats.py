"""
modules/stats.py — /stats command.
Shows global bot statistics.
"""
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import (
    application, collection, user_collection,
    top_global_groups_collection, group_user_totals_collection,
    pm_users, StartTime, OWNER_ID, sudo_users,
)


async def stats(update: Update, context: CallbackContext) -> None:
    msg = await update.message.reply_text("⏳ Stats တွက်နေတယ်...")

    try:
        total_chars  = await collection.count_documents({})
        total_users  = await user_collection.count_documents({})
        total_pm     = await pm_users.count_documents({})

        g1 = set(await top_global_groups_collection.distinct("group_id"))
        g2 = set(await group_user_totals_collection.distinct("group_id"))
        total_groups = len(g1 | g2)

        uptime_sec = int(time.time() - StartTime)
        days, rem  = divmod(uptime_sec, 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        uptime_str = f"{days}d {hours}h {mins}m {secs}s"

        text = (
            "📊 <b>Bot Statistics</b>\n\n"
            f"🌸 Characters   : <b>{total_chars:,}</b>\n"
            f"👤 Users        : <b>{total_users:,}</b>\n"
            f"💬 PM Users     : <b>{total_pm:,}</b>\n"
            f"👥 Groups       : <b>{total_groups:,}</b>\n\n"
            f"⏱ Uptime       : <b>{uptime_str}</b>\n"
            f"🛡 Sudo Users   : <b>{len(sudo_users)}</b>"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")


application.add_handler(CommandHandler("stats", stats, block=False))
