import asyncio
from telegram import Update
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


async def broadcast(update: Update, context: CallbackContext) -> None:
    """Reply to any message + /broadcast — forwards to all known groups & PM users."""
    if not _is_auth(update.effective_user.id):
        await update.message.reply_text("❌ Owner only.")
        return

    src = update.message.reply_to_message
    if not src:
        await update.message.reply_text(
            "📢 Usage: <b>Reply</b> to a message, then send /broadcast",
            parse_mode="HTML",
        )
        return

    # ── Collect all known group IDs from every collection ────────────────────
    g1 = set(await top_global_groups_collection.distinct("group_id"))
    g2 = set(await group_user_totals_collection.distinct("group_id"))
    g3 = set(await user_totals_collection.distinct("chat_id"))
    all_groups = list(g1 | g2 | g3)

    # ── PM users ─────────────────────────────────────────────────────────────
    all_pms = await pm_users.distinct("_id")

    targets = all_groups + all_pms
    total   = len(targets)

    if total == 0:
        await update.message.reply_text(
            "⚠️ No known groups or PM users yet. "
            "Wait for users to interact with the bot first."
        )
        return

    status = await update.message.reply_text(
        f"📢 Broadcasting to <b>{total}</b> targets "
        f"({len(all_groups)} groups + {len(all_pms)} PMs)…",
        parse_mode="HTML",
    )

    tasks = [
        _copy(context.bot, t, src.chat_id, src.message_id)
        for t in targets
    ]
    res      = await asyncio.gather(*tasks)
    ok, fail = sum(res), res.count(False)

    await status.edit_text(
        f"✅ Broadcast ပြီးပြီ!\n"
        f"✔️ Delivered: <b>{ok}</b>\n"
        f"❌ Failed:    <b>{fail}</b>\n"
        f"📦 Total:     <b>{total}</b>",
        parse_mode="HTML",
    )


application.add_handler(CommandHandler("broadcast", broadcast, block=False))
