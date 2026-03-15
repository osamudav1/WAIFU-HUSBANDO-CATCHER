import asyncio
from telegram import Update
from telegram.error import Forbidden, BadRequest
from telegram.ext import CallbackContext, CommandHandler
from waifu import application, top_global_groups_collection, pm_users, OWNER_ID, LOGGER

_SEM   = asyncio.Semaphore(20)
_DELAY = 0.05


async def _copy(bot, chat_id: int, from_chat: int, msg_id: int) -> bool:
    async with _SEM:
        try:
            await bot.copy_message(chat_id, from_chat, msg_id)
            await asyncio.sleep(_DELAY)
            return True
        except (Forbidden, BadRequest) as e:
            LOGGER.debug("Broadcast skip %s: %s", chat_id, e)
        except Exception as e:
            LOGGER.warning("Broadcast err %s: %s", chat_id, e)
    return False


async def broadcast(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only."); return
    src = update.message.reply_to_message
    if not src:
        await update.message.reply_text("Reply to a message to broadcast it."); return

    groups = await top_global_groups_collection.distinct("group_id")
    users  = await pm_users.distinct("_id")
    total  = len(groups) + len(users)
    status = await update.message.reply_text(f"📢 Broadcasting to {total} targets…")

    tasks  = [_copy(context.bot, t, src.chat_id, src.message_id) for t in groups + users]
    res    = await asyncio.gather(*tasks)
    ok, fail = sum(res), res.count(False)
    await status.edit_text(
        f"✅ Done!\n✔️ {ok} delivered\n❌ {fail} failed"
    )


application.add_handler(CommandHandler("broadcast", broadcast, block=False))
