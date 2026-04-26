import asyncio

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from telegram.ext import CallbackContext, CommandHandler
from waifu import (
    application,
    top_global_groups_collection,
    group_user_totals_collection,
    user_totals_collection,
    pm_users,
    OWNER_ID,
    LOGGER,
    sudo_users,
)

_SEM   = asyncio.Semaphore(20)
_DELAY = 0.05


def _is_auth(uid: int) -> bool:
    return uid == OWNER_ID or uid in sudo_users


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


async def _send_text(bot, chat_id: int, text: str) -> bool:
    async with _SEM:
        try:
            await bot.send_message(chat_id, text)
            await asyncio.sleep(_DELAY)
            return True
        except (Forbidden, BadRequest) as e:
            LOGGER.debug("Broadcast skip %s: %s", chat_id, e)
        except Exception as e:
            LOGGER.warning("Broadcast err %s: %s", chat_id, e)
    return False


async def broadcast(update: Update, context: CallbackContext) -> None:
    """Reply to any message + /broadcast — OR /broadcast <text> — sends to all groups & PMs."""
    if not _is_auth(update.effective_user.id):
        await update.message.reply_text("❌ Owner only.")
        return

    src      = update.message.reply_to_message
    txt_args = " ".join(context.args).strip() if context.args else ""

    if not src and not txt_args:
        await update.message.reply_text(
            "📢 <b>Broadcast Usage:</b>\n\n"
            "① Message ကို <b>reply</b> လုပ်ပြီး <code>/broadcast</code> ပို့ပေး\n"
            "② သို့မဟုတ် <code>/broadcast မင်္ဂလာပါ everyone!</code> ရိုက်ပေး",
            parse_mode=ParseMode.HTML,
        )
        return

    g1 = set(await top_global_groups_collection.distinct("group_id"))
    g2 = set(await group_user_totals_collection.distinct("group_id"))
    all_groups = list(g1 | g2)
    all_pms    = await pm_users.distinct("_id")
    targets    = list(set(all_groups + list(all_pms)))
    total      = len(targets)

    if total == 0:
        await update.message.reply_text("⚠️ Known groups/PMs မရှိသေး — restore backup ဦး")
        return

    status = await update.message.reply_text(
        f"📢 Broadcasting to <b>{total}</b> targets "
        f"({len(all_groups)} groups + {len(all_pms)} PMs)…",
        parse_mode=ParseMode.HTML,
    )

    if src:
        tasks = [_copy(context.bot, t, src.chat_id, src.message_id) for t in targets]
    else:
        tasks = [_send_text(context.bot, t, txt_args) for t in targets]

    res      = await asyncio.gather(*tasks)
    ok, fail = sum(res), res.count(False)

    await status.edit_text(
        f"✅ Broadcast ပြီးပြီ!\n"
        f"✔️ Delivered: <b>{ok}</b>\n"
        f"❌ Failed:    <b>{fail}</b>\n"
        f"📦 Total:     <b>{total}</b>",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("broadcast", broadcast, block=False))
