"""
modules/changetime.py — /changetime <minutes> per-group drop interval.

• In a group  : admin / owner / sudo → /changetime <minutes>
• In owner DM : /changetime              → list all groups + intervals
              : /changetime <group_id> <minutes> → set specific group
              : /resettime <group_id>   → reset specific group
              : /resettime              → list all
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


# ── helper: show all groups list ─────────────────────────────────────────────

async def _list_all_groups(bot, reply_fn) -> None:
    docs = await user_totals_collection.find(
        {"chat_id": {"$lt": 0}}
    ).to_list(length=500)

    if not docs:
        await reply_fn("📋 DB မှာ group settings မရှိသေးပါ။")
        return

    lines = ["📋 <b>Group Drop Intervals</b>\n"]
    for d in docs:
        cid = d["chat_id"]
        mins = d.get("drop_interval_minutes", Config.DROP_INTERVAL_MIN)
        try:
            chat = await bot.get_chat(cid)
            name = chat.title or str(cid)
        except Exception:
            name = str(cid)
        lines.append(f"• <code>{cid}</code> | <b>{name}</b> → <b>{mins}</b> min")

    await reply_fn("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /changetime ───────────────────────────────────────────────────────────────

async def changetime(update: Update, context: CallbackContext) -> None:
    uid  = update.effective_user.id
    chat = update.effective_chat

    # ── Owner DM mode ─────────────────────────────────────────────────────────
    if chat.type == "private":
        if uid != OWNER_ID:
            await update.message.reply_text("❌ Owner only.")
            return

        args = context.args or []

        # /changetime  →  list all groups
        if not args:
            await _list_all_groups(
                context.bot,
                lambda text, **kw: update.message.reply_text(text, **kw),
            )
            return

        # /changetime <minutes>  →  set ALL groups to that interval
        if len(args) == 1:
            if not args[0].lstrip("-").isdigit():
                await update.message.reply_text(
                    "သုံးပုံ:\n"
                    "<code>/changetime &lt;minutes&gt;</code> — group အားလုံးကို သတ်မှတ်\n"
                    "<code>/changetime &lt;group_id&gt; &lt;minutes&gt;</code> — group တစ်ခုကို သတ်မှတ်\n"
                    "<code>/changetime</code> — group list ကြည့်",
                    parse_mode=ParseMode.HTML,
                )
                return
            n = int(args[0])
            if n < _MIN:
                await update.message.reply_text(f"❌ အနည်းဆုံး {_MIN} မိနစ်.")
                return
            if n > _MAX:
                await update.message.reply_text(f"❌ အများဆုံး {_MAX} မိနစ် (24 နာရီ).")
                return

            # Collect all known group IDs from the three collections
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

            from waifu.modules.waifu_drop import restart_drop_task
            for gid in all_groups:
                await user_totals_collection.find_one_and_update(
                    {"chat_id": gid},
                    {"$set": {"drop_interval_minutes": n}},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
                restart_drop_task(gid, context.bot)

            await update.message.reply_text(
                f"✅ Group <b>{len(all_groups)}</b> ခုအကုန်လုံး\n"
                f"Drop interval → <b>{n}</b> မိနစ်တိုင်း drop\n"
                f"<i>✨ ချက်ချင်း apply ဖြစ်ပြီ (loop restart ပြီး)</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        # /changetime <group_id> <minutes>  →  set one specific group
        group_id_str, minutes_str = args[0], args[1]
        if not group_id_str.lstrip("-").isdigit() or not minutes_str.lstrip("-").isdigit():
            await update.message.reply_text("❌ /changetime <group_id> <minutes>")
            return

        target_cid = int(group_id_str)
        n          = int(minutes_str)
        if n < _MIN:
            await update.message.reply_text(f"❌ အနည်းဆုံး {_MIN} မိနစ်.")
            return
        if n > _MAX:
            await update.message.reply_text(f"❌ အများဆုံး {_MAX} မိနစ် (24 နာရီ).")
            return

        old = await get_interval(target_cid)
        await user_totals_collection.find_one_and_update(
            {"chat_id": target_cid},
            {"$set": {"drop_interval_minutes": n}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        from waifu.modules.waifu_drop import restart_drop_task
        restart_drop_task(target_cid, context.bot)
        try:
            chat_obj = await context.bot.get_chat(target_cid)
            gname = chat_obj.title or str(target_cid)
        except Exception:
            gname = str(target_cid)

        await update.message.reply_text(
            f"✅ <b>{gname}</b>\n"
            f"Drop interval: <b>{old}</b> → <b>{n}</b> မိနစ်တိုင်း drop\n"
            f"<i>✨ ချက်ချင်း apply ဖြစ်ပြီ</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Group mode ────────────────────────────────────────────────────────────
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        cur = await get_interval(chat.id)
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

    old = await get_interval(chat.id)
    await user_totals_collection.find_one_and_update(
        {"chat_id": chat.id},
        {"$set": {"drop_interval_minutes": n}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    from waifu.modules.waifu_drop import restart_drop_task
    restart_drop_task(chat.id, context.bot)
    await update.message.reply_text(
        f"✅ Drop interval: <b>{old}</b> → <b>{n}</b> မိနစ်တိုင်း drop\n"
        f"<i>✨ ချက်ချင်း apply ဖြစ်ပြီ</i>",
        parse_mode=ParseMode.HTML,
    )


# ── /resettime ────────────────────────────────────────────────────────────────

async def resettime(update: Update, context: CallbackContext) -> None:
    uid  = update.effective_user.id
    chat = update.effective_chat

    # ── Owner DM mode ─────────────────────────────────────────────────────────
    if chat.type == "private":
        if uid != OWNER_ID:
            await update.message.reply_text("❌ Owner only.")
            return

        args = context.args or []

        # /resettime  →  reset ALL groups to default
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
                    {"$unset": {"drop_interval_minutes": ""}},
                )
            await update.message.reply_text(
                f"✅ Group <b>{len(all_groups)}</b> ခုအကုန်လုံး\n"
                f"Default ပြန်သတ်မှတ်ပြီ: <b>{Config.DROP_INTERVAL_MIN}</b> မိနစ်တိုင်း drop",
                parse_mode=ParseMode.HTML,
            )
            return

        if not args[0].lstrip("-").isdigit():
            await update.message.reply_text(
                "သုံးပုံ: <code>/resettime &lt;group_id&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        target_cid = int(args[0])
        await user_totals_collection.update_one(
            {"chat_id": target_cid},
            {"$unset": {"drop_interval_minutes": ""}},
        )
        try:
            chat_obj = await context.bot.get_chat(target_cid)
            gname = chat_obj.title or str(target_cid)
        except Exception:
            gname = str(target_cid)

        await update.message.reply_text(
            f"✅ <b>{gname}</b> — Default ပြန်သတ်မှတ်ပြီ:\n"
            f"<b>{Config.DROP_INTERVAL_MIN}</b> မိနစ်တိုင်း drop",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Group mode ────────────────────────────────────────────────────────────
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admins only.")
        return

    await user_totals_collection.update_one(
        {"chat_id": chat.id},
        {"$unset": {"drop_interval_minutes": ""}},
    )
    await update.message.reply_text(
        f"✅ Default ပြန်သတ်မှတ်ပြီ: <b>{Config.DROP_INTERVAL_MIN}</b> မိနစ်တိုင်း drop",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler(["changetime"], changetime))
application.add_handler(CommandHandler(["resettime"],  resettime))
