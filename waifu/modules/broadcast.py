import asyncio
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from telegram.ext import CallbackContext, CommandHandler
from waifu import (
    application,
    top_global_groups_collection,
    group_user_totals_collection,
    user_totals_collection,
    user_collection,
    pm_users,
    OWNER_ID,
    LOGGER,
    sudo_users,
)

_SEM   = asyncio.Semaphore(20)
_DELAY = 0.05

GOD_BADGE  = "🌌 God Of Waifu"
UL_RARITY  = "🌌 Universal Limited"


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


async def _send_msg(bot, chat_id: int, text: str) -> bool:
    async with _SEM:
        try:
            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            await asyncio.sleep(_DELAY)
            return True
        except (Forbidden, BadRequest) as e:
            LOGGER.debug("3★ noti skip %s: %s", chat_id, e)
        except Exception as e:
            LOGGER.warning("3★ noti err %s: %s", chat_id, e)
    return False


async def broadcast(update: Update, context: CallbackContext) -> None:
    """
    /broadcast              — reply to a message → forward to all groups & PMs
    /broadcast <char_id>    — send 3★ achievement noti for all users who own
                              that character at 3★, to every known group
    """
    if not _is_auth(update.effective_user.id):
        await update.message.reply_text("❌ Owner only.")
        return

    # ── Mode 1: /broadcast <char_id> ─────────────────────────────────────────
    if context.args:
        char_id = context.args[0].strip()

        # Find character info from any user who owns it
        owner_doc = await user_collection.find_one(
            {"characters.id": char_id},
            {"characters.$": 1},
        )
        if not owner_doc or not owner_doc.get("characters"):
            await update.message.reply_text(
                f"❌ Character <code>{escape(char_id)}</code> မတွေ့ဘူး",
                parse_mode=ParseMode.HTML,
            )
            return

        char     = owner_doc["characters"][0]
        char_name = escape(char.get("name", char_id))
        rarity   = char.get("rarity", "")

        # Find all users with 3★ on this character
        star_field = f"waifu_stars.{char_id}"
        three_star_users = await user_collection.find(
            {star_field: 3},
            {"id": 1, "first_name": 1},
        ).to_list(length=5000)

        if not three_star_users:
            await update.message.reply_text(
                f"⚠️ <b>{char_name}</b> ကို 3★ ရှိတဲ့ user မရှိသေး",
                parse_mode=ParseMode.HTML,
            )
            return

        # Get all known group IDs
        group_docs = await top_global_groups_collection.find(
            {}, {"group_id": 1}
        ).to_list(length=500)
        group_ids = list({d["group_id"] for d in group_docs if "group_id" in d})

        if not group_ids:
            await update.message.reply_text("⚠️ Known group မရှိသေး")
            return

        status = await update.message.reply_text(
            f"🌟 <b>{char_name}</b> 3★ noti broadcast လုပ်နေပြီ…\n"
            f"👥 Users: <b>{len(three_star_users)}</b> | 📡 Groups: <b>{len(group_ids)}</b>",
            parse_mode=ParseMode.HTML,
        )

        ok = fail = 0
        for u in three_star_users:
            uid  = u["id"]
            fn   = escape(u.get("first_name", "Someone"))
            mention = f'<a href="tg://user?id={uid}">{fn}</a>'

            if rarity == UL_RARITY:
                ann_text = (
                    f"🌌 <b>GOD OF WAIFU!</b>\n\n"
                    f"{mention} has reached <b>3★</b> on a "
                    f"<b>{rarity}</b> character!\n"
                    f"🏅 Badge awarded: <b>{GOD_BADGE}</b>"
                )
            else:
                ann_text = (
                    f"✨ <b>3★ Achieved!</b>\n\n"
                    f"{mention} has reached <b>3★</b> on a "
                    f"<b>{rarity}</b> character!"
                )

            tasks = [_send_msg(context.bot, gid, ann_text) for gid in group_ids]
            res   = await asyncio.gather(*tasks)
            ok   += sum(res)
            fail += res.count(False)

        await status.edit_text(
            f"✅ 3★ Noti broadcast ပြီးပြီ!\n"
            f"👤 Users notified: <b>{len(three_star_users)}</b>\n"
            f"✔️ Delivered: <b>{ok}</b>\n"
            f"❌ Failed:    <b>{fail}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Mode 2: reply-based regular broadcast ────────────────────────────────
    src = update.message.reply_to_message
    if not src:
        await update.message.reply_text(
            "📢 Usage:\n"
            "• <b>Reply</b> to a message + <code>/broadcast</code> → all groups & PMs\n"
            "• <code>/broadcast &lt;char_id&gt;</code> → 3★ noti to all groups",
            parse_mode=ParseMode.HTML,
        )
        return

    g1 = set(await top_global_groups_collection.distinct("group_id"))
    g2 = set(await group_user_totals_collection.distinct("group_id"))
    g3 = set(await user_totals_collection.distinct("chat_id"))
    all_groups = list(g1 | g2 | g3)
    all_pms    = await pm_users.distinct("_id")
    targets    = all_groups + all_pms
    total      = len(targets)

    if total == 0:
        await update.message.reply_text("⚠️ Known groups/PMs မရှိသေး")
        return

    status = await update.message.reply_text(
        f"📢 Broadcasting to <b>{total}</b> targets "
        f"({len(all_groups)} groups + {len(all_pms)} PMs)…",
        parse_mode=ParseMode.HTML,
    )

    tasks = [_copy(context.bot, t, src.chat_id, src.message_id) for t in targets]
    res   = await asyncio.gather(*tasks)
    ok, fail = sum(res), res.count(False)

    await status.edit_text(
        f"✅ Broadcast ပြီးပြီ!\n"
        f"✔️ Delivered: <b>{ok}</b>\n"
        f"❌ Failed:    <b>{fail}</b>\n"
        f"📦 Total:     <b>{total}</b>",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("broadcast", broadcast, block=False))
