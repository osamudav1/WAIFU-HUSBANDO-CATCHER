"""
modules/changetime.py — /setdropcount <N> per-group message drop threshold.

Every N messages in a group → character drop.

• In a group  : admin / owner / sudo → /setdropcount <N>
• In owner DM : /setdropcount              → list all groups + thresholds
              : /setdropcount <group_id> <N> → set specific group
              : /resetdropcount <group_id>   → reset to default (10)
"""
from pymongo import ReturnDocument
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, CallbackContext

from waifu import application, user_totals_collection, sudo_users, OWNER_ID
from waifu.modules.waifu_drop import _DROP_MSG_DEFAULT, restart_drop_task

_MIN, _MAX = 1, 9999


async def _is_admin(update: Update, context: CallbackContext) -> bool:
    uid = update.effective_user.id
    if uid == OWNER_ID or uid in sudo_users:
        return True
    m = await context.bot.get_chat_member(update.effective_chat.id, uid)
    return m.status in ("administrator", "creator")


async def get_threshold(chat_id: int) -> int:
    """Return message drop threshold for chat_id (DB value or default)."""
    doc = await user_totals_collection.find_one({"chat_id": chat_id})
    if doc and "drop_msg_count" in doc:
        return int(doc["drop_msg_count"])
    return _DROP_MSG_DEFAULT


async def _list_all_groups(bot, reply_fn) -> None:
    docs = await user_totals_collection.find(
        {"chat_id": {"$lt": 0}}
    ).to_list(length=500)

    if not docs:
        await reply_fn("📋 DB မှာ group settings မရှိသေးပါ။")
        return

    lines = ["📋 <b>Group Drop Thresholds</b>\n"]
    for d in docs:
        cid = d["chat_id"]
        cnt = d.get("drop_msg_count", _DROP_MSG_DEFAULT)
        try:
            chat = await bot.get_chat(cid)
            name = chat.title or str(cid)
        except Exception:
            name = str(cid)
        lines.append(f"• <code>{cid}</code> | <b>{name}</b> → <b>{cnt}</b> messages")

    await reply_fn("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /setdropcount ──────────────────────────────────────────────────────────────

async def setdropcount(update: Update, context: CallbackContext) -> None:
    uid  = update.effective_user.id
    chat = update.effective_chat

    # ── Owner DM mode ─────────────────────────────────────────────────────────
    if chat.type == "private":
        if uid != OWNER_ID:
            await update.message.reply_text("❌ Owner only.")
            return

        args = context.args or []

        if not args:
            await _list_all_groups(
                context.bot,
                lambda text, **kw: update.message.reply_text(text, **kw),
            )
            return

        if len(args) == 1:
            if not args[0].isdigit():
                await update.message.reply_text(
                    "သုံးပုံ:\n"
                    "<code>/setdropcount &lt;N&gt;</code> — group အားလုံးကို သတ်မှတ်\n"
                    "<code>/setdropcount &lt;group_id&gt; &lt;N&gt;</code> — group တစ်ခုကို\n"
                    "<code>/setdropcount</code> — group list ကြည့်",
                    parse_mode=ParseMode.HTML,
                )
                return
            n = int(args[0])
            if not (_MIN <= n <= _MAX):
                await update.message.reply_text(f"❌ {_MIN}–{_MAX} ထဲမှာ ထည့်ပေး.")
                return

            from waifu import (
                top_global_groups_collection,
                group_user_totals_collection,
            )
            g1 = set(await top_global_groups_collection.distinct("group_id"))
            g2 = set(await group_user_totals_collection.distinct("group_id"))
            g3 = set(
                d["chat_id"]
                for d in await user_totals_collection.find(
                    {"chat_id": {"$lt": 0}}
                ).to_list(length=1000)
            )
            all_groups = list(g1 | g2 | g3)

            for gid in all_groups:
                await user_totals_collection.find_one_and_update(
                    {"chat_id": gid},
                    {"$set": {"drop_msg_count": n}},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
                restart_drop_task(gid, context.bot)

            await update.message.reply_text(
                f"✅ Group <b>{len(all_groups)}</b> ခုအကုန်လုံး\n"
                f"Message <b>{n}</b> ​စောင်တိုင်း drop ကျမည်\n"
                f"<i>✨ ချက်ချင်း apply ဖြစ်ပြီ</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        group_id_str, count_str = args[0], args[1]
        if not group_id_str.lstrip("-").isdigit() or not count_str.isdigit():
            await update.message.reply_text("❌ /setdropcount <group_id> <N>")
            return

        target_cid = int(group_id_str)
        n          = int(count_str)
        if not (_MIN <= n <= _MAX):
            await update.message.reply_text(f"❌ {_MIN}–{_MAX} ထဲမှာ ထည့်ပေး.")
            return

        old = await get_threshold(target_cid)
        await user_totals_collection.find_one_and_update(
            {"chat_id": target_cid},
            {"$set": {"drop_msg_count": n}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        restart_drop_task(target_cid, context.bot)
        try:
            chat_obj = await context.bot.get_chat(target_cid)
            gname = chat_obj.title or str(target_cid)
        except Exception:
            gname = str(target_cid)

        await update.message.reply_text(
            f"✅ <b>{gname}</b>\n"
            f"Drop threshold: <b>{old}</b> → <b>{n}</b> messages တိုင်း drop\n"
            f"<i>✨ ချက်ချင်း apply ဖြစ်ပြီ</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Group mode ────────────────────────────────────────────────────────────
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only.")
        return

    if not context.args or not context.args[0].isdigit():
        cur = await get_threshold(chat.id)
        await update.message.reply_text(
            f"💬 လောကြိုက်: <b>{cur}</b> messages တိုင်း drop ကျမည်\n"
            f"သုံးပုံ: /setdropcount &lt;{_MIN}–{_MAX}&gt;\n"
            f"ပြန် reset: /resetdropcount",
            parse_mode=ParseMode.HTML,
        )
        return

    n = int(context.args[0])
    if not (_MIN <= n <= _MAX):
        await update.message.reply_text(f"❌ {_MIN}–{_MAX} ထဲမှာ ထည့်ပေး.")
        return

    old = await get_threshold(chat.id)
    await user_totals_collection.find_one_and_update(
        {"chat_id": chat.id},
        {"$set": {"drop_msg_count": n}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    restart_drop_task(chat.id, context.bot)
    await update.message.reply_text(
        f"✅ Drop threshold: <b>{old}</b> → <b>{n}</b> messages တိုင်း drop\n"
        f"<i>✨ ချက်ချင်း apply ဖြစ်ပြီ</i>",
        parse_mode=ParseMode.HTML,
    )


# ── /resetdropcount ────────────────────────────────────────────────────────────

async def resetdropcount(update: Update, context: CallbackContext) -> None:
    uid  = update.effective_user.id
    chat = update.effective_chat

    if chat.type == "private":
        if uid != OWNER_ID:
            await update.message.reply_text("❌ Owner only.")
            return

        args = context.args or []

        if not args:
            from waifu import (
                top_global_groups_collection,
                group_user_totals_collection,
            )
            g1 = set(await top_global_groups_collection.distinct("group_id"))
            g2 = set(await group_user_totals_collection.distinct("group_id"))
            g3 = set(
                d["chat_id"]
                for d in await user_totals_collection.find(
                    {"chat_id": {"$lt": 0}}
                ).to_list(length=1000)
            )
            all_groups = list(g1 | g2 | g3)
            for gid in all_groups:
                await user_totals_collection.update_one(
                    {"chat_id": gid},
                    {"$unset": {"drop_msg_count": ""}},
                )
                restart_drop_task(gid, context.bot)
            await update.message.reply_text(
                f"✅ Group <b>{len(all_groups)}</b> ခုအကုန်လုံး\n"
                f"Default ပြန်သတ်မှတ်ပြီ: <b>{_DROP_MSG_DEFAULT}</b> messages တိုင်း drop",
                parse_mode=ParseMode.HTML,
            )
            return

        if not args[0].lstrip("-").isdigit():
            await update.message.reply_text(
                "သုံးပုံ: <code>/resetdropcount &lt;group_id&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        target_cid = int(args[0])
        await user_totals_collection.update_one(
            {"chat_id": target_cid},
            {"$unset": {"drop_msg_count": ""}},
        )
        restart_drop_task(target_cid, context.bot)
        try:
            chat_obj = await context.bot.get_chat(target_cid)
            gname = chat_obj.title or str(target_cid)
        except Exception:
            gname = str(target_cid)

        await update.message.reply_text(
            f"✅ <b>{gname}</b> — Default ပြန်သတ်မှတ်ပြီ:\n"
            f"<b>{_DROP_MSG_DEFAULT}</b> messages တိုင်း drop",
            parse_mode=ParseMode.HTML,
        )
        return

    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only.")
        return

    await user_totals_collection.update_one(
        {"chat_id": chat.id},
        {"$unset": {"drop_msg_count": ""}},
    )
    restart_drop_task(chat.id, context.bot)
    await update.message.reply_text(
        f"✅ Default ပြန်သတ်မှတ်ပြီ: <b>{_DROP_MSG_DEFAULT}</b> messages တိုင်း drop",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler(["setdropcount", "changetime"], setdropcount))
application.add_handler(CommandHandler(["resetdropcount", "resettime"],  resetdropcount))
