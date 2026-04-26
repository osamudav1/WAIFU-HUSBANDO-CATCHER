"""
modules/backup.py — Owner-only full backup & restore.

/backup  — exports ALL bot data (characters + users) as JSON → sends to owner DM
/restore — owner sends the backup JSON file → bot restores (with confirmation)

Only works in owner's private DM.
"""
import io
import json
from datetime import datetime, timezone

from bson import ObjectId
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackContext, CallbackQueryHandler, CommandHandler, MessageHandler, filters,
)


from waifu import (
    application, db, collection, user_collection,
    user_totals_collection, group_user_totals_collection,
    top_global_groups_collection, pm_users, OWNER_ID,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_owner_pm(update: Update) -> bool:
    return (
        update.effective_user.id == OWNER_ID
        and update.effective_chat.type == "private"
    )


def _serialize(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj)}")


async def _dump_collection(col) -> list:
    docs = []
    async for doc in col.find({}):
        doc.pop("_id", None)
        docs.append(doc)
    return docs


# ── /backup — menu ─────────────────────────────────────────────────────────────

async def backup_cmd(update: Update, context: CallbackContext) -> None:
    if not _is_owner_pm(update):
        return

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Backup",  callback_data="backup:do"),
            InlineKeyboardButton("📥 Restore", callback_data="backup:restore_prompt"),
        ]
    ])
    await update.message.reply_text(
        "🗄️ <b>Backup / Restore</b>\n\n"
        "📦 <b>Backup</b> — Bot data အကုန် JSON file ထုတ်ပြီး PM ပို့မည်\n"
        "📥 <b>Restore</b> — JSON file ပို့ပြီး data ပြန်ထည့်မည်",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def _do_backup(update: Update, context: CallbackContext) -> None:
    msg = await update.message.reply_text("⏳ Backup လုပ်နေတယ်... ခဏစောင့်ပေး")

    try:
        data = {
            "version":    "2.0",
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "characters": await _dump_collection(collection),
            "users":      await _dump_collection(user_collection),
            "chat_settings":       await _dump_collection(user_totals_collection),
            "group_user_totals":   await _dump_collection(group_user_totals_collection),
            "top_groups":          await _dump_collection(top_global_groups_collection),
            "pm_users":            await _dump_collection(pm_users),
        }

        total_chars = len(data["characters"])
        total_users = len(data["users"])

        raw  = json.dumps(data, ensure_ascii=False, indent=2, default=_serialize)
        buf  = io.BytesIO(raw.encode("utf-8"))
        now  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"waifu_backup_{now}.json"
        buf.name = fname

        await msg.delete()
        await update.message.reply_document(
            document=buf,
            filename=fname,
            caption=(
                f"✅ <b>Backup ပြီးပြီ!</b>\n\n"
                f"🌸 Characters : <b>{total_chars}</b>\n"
                f"👤 Users      : <b>{total_users}</b>\n"
                f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
                f"<i>Restore လုပ်ဖို့ ဒီ file ကို bot PM မှာ forward/send ပြီး /restore နှိပ်</i>"
            ),
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        await msg.edit_text(f"❌ Backup မအောင်မြင်ဘူး: {e}")


# ── backup menu callbacks ───────────────────────────────────────────────────────

async def backup_menu_callback(update: Update, context: CallbackContext) -> None:
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()

    if uid != OWNER_ID:
        return

    if q.data == "backup:do":
        await q.edit_message_text("⏳ Backup လုပ်နေတယ်... ခဏစောင့်ပေး")
        try:
            data = {
                "version":    "2.0",
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "characters": await _dump_collection(collection),
                "users":      await _dump_collection(user_collection),
                "chat_settings":       await _dump_collection(user_totals_collection),
                "group_user_totals":   await _dump_collection(group_user_totals_collection),
                "top_groups":          await _dump_collection(top_global_groups_collection),
                "pm_users":            await _dump_collection(pm_users),
            }
            total_chars = len(data["characters"])
            total_users = len(data["users"])
            raw   = json.dumps(data, ensure_ascii=False, indent=2, default=_serialize)
            buf   = io.BytesIO(raw.encode("utf-8"))
            now   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            fname = f"waifu_backup_{now}.json"
            buf.name = fname
            await q.message.delete()
            await context.bot.send_document(
                chat_id=uid,
                document=buf,
                filename=fname,
                caption=(
                    f"✅ <b>Backup ပြီးပြီ!</b>\n\n"
                    f"🌸 Characters : <b>{total_chars}</b>\n"
                    f"👤 Users      : <b>{total_users}</b>\n"
                    f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
                    f"<i>Restore လုပ်ဖို့ ဒီ file ကို /restore နှိပ်ပြီး attach လုပ်ပေး</i>"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            await q.edit_message_text(f"❌ Backup မအောင်မြင်ဘူး: {e}")

    elif q.data == "backup:restore_prompt":
        await q.edit_message_text(
            "📥 <b>Restore</b>\n\n"
            "Backup JSON file ကို attach လုပ်ပြီး <b>/restore</b> command ရိုက်ပေး\n\n"
            "<i>(သို့မဟုတ်) JSON file message ကို /restore ဖြင့် reply လုပ်ပေး</i>",
            parse_mode=ParseMode.HTML,
        )


# ── /restore ───────────────────────────────────────────────────────────────────

_PENDING: dict[int, dict] = {}   # user_id → parsed backup dict


async def restore_cmd(update: Update, context: CallbackContext) -> None:
    if not _is_owner_pm(update):
        return

    doc = update.message.document
    if not doc:
        await update.message.reply_text(
            "📎 Backup JSON file ကို attach လုပ်ပြီး /restore ထည့်ပေး\n"
            "<i>(သို့) /restore ဆိုတဲ့ message ကို JSON file message ကနေ reply လုပ်ပေး</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not doc.file_name.endswith(".json"):
        await update.message.reply_text("❌ JSON file တစ်ခုထည့်ပေး")
        return

    msg = await update.message.reply_text("⏳ File ဖတ်နေတယ်...")
    try:
        file_obj = await doc.get_file()
        raw      = await file_obj.download_as_bytearray()
        data     = json.loads(raw.decode("utf-8"))

        if "characters" not in data or "users" not in data:
            await msg.edit_text("❌ Invalid backup file — characters/users data မပါဘူး")
            return

        n_chars = len(data["characters"])
        n_users = len(data["users"])
        ts      = data.get("timestamp", "unknown")

        _PENDING[update.effective_user.id] = data

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ အတည်ပြုမည်",  callback_data="restore:confirm"),
                InlineKeyboardButton("❌ ပယ်ဖျက်မည်", callback_data="restore:cancel"),
            ]
        ])
        await msg.edit_text(
            f"⚠️ <b>Restore အတည်ပြုပေး!</b>\n\n"
            f"🌸 Characters : <b>{n_chars}</b>\n"
            f"👤 Users      : <b>{n_users}</b>\n"
            f"🕒 Backup Date: <b>{ts[:19].replace('T',' ')} UTC</b>\n\n"
            f"<b>⚠️ ယခု DB ထဲမှာရှိတဲ့ data အကုန် overwrite ဖြစ်သွားမည်!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    except json.JSONDecodeError:
        await msg.edit_text("❌ JSON parse မရဘူး — file ပျက်နေနိုင်တယ်")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")


async def restore_callback(update: Update, context: CallbackContext) -> None:
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()

    if uid != OWNER_ID:
        return

    if q.data == "restore:cancel":
        _PENDING.pop(uid, None)
        await q.edit_message_text("❌ Restore ပယ်ဖျက်လိုက်တယ်")
        return

    data = _PENDING.pop(uid, None)
    if not data:
        await q.edit_message_text("❌ Pending backup မရှိဘူး — ထပ်ကြိုးစား")
        return

    await q.edit_message_text("⏳ Restore လုပ်နေတယ်... ခဏစောင့်ပေး")

    try:
        col_map = {
            "characters":         collection,
            "users":              user_collection,
            "chat_settings":      user_totals_collection,
            "group_user_totals":  group_user_totals_collection,
            "top_groups":         top_global_groups_collection,
            "pm_users":           pm_users,
        }

        restored = {}
        for key, col in col_map.items():
            docs = data.get(key, [])
            if not docs:
                restored[key] = 0
                continue
            await col.delete_many({})
            result = await col.insert_many(docs)
            restored[key] = len(result.inserted_ids)

        await q.edit_message_text(
            f"✅ <b>Restore ပြီးပြီ!</b>\n\n"
            f"🌸 Characters    : <b>{restored.get('characters', 0)}</b>\n"
            f"👤 Users         : <b>{restored.get('users', 0)}</b>\n"
            f"💬 Chat Settings : <b>{restored.get('chat_settings', 0)}</b>\n"
            f"👥 Group Totals  : <b>{restored.get('group_user_totals', 0)}</b>",
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        await q.edit_message_text(f"❌ Restore မအောင်မြင်ဘူး: {e}")


# ── Auto-detect JSON restore file ─────────────────────────────────────────────

async def _auto_restore_detect(update: Update, context: CallbackContext) -> None:
    """Owner sends a JSON file in PM → auto-trigger restore flow."""
    if update.effective_user.id != OWNER_ID:
        return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".json"):
        return

    msg = await update.message.reply_text("⏳ Backup file ဖတ်နေတယ်...")
    try:
        file_obj = await doc.get_file()
        raw      = await file_obj.download_as_bytearray()
        data     = json.loads(raw.decode("utf-8"))

        if "characters" not in data or "users" not in data:
            await msg.edit_text("❌ Invalid backup file — characters/users data မပါဘူး")
            return

        n_chars = len(data["characters"])
        n_users = len(data["users"])
        ts      = data.get("timestamp", "unknown")[:19].replace("T", " ")

        _PENDING[update.effective_user.id] = data

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ အတည်ပြုမည်",  callback_data="restore:confirm"),
                InlineKeyboardButton("❌ ပယ်ဖျက်မည်", callback_data="restore:cancel"),
            ]
        ])
        await msg.edit_text(
            f"⚠️ <b>Restore အတည်ပြုပေး!</b>\n\n"
            f"🌸 Characters : <b>{n_chars}</b>\n"
            f"👤 Users      : <b>{n_users}</b>\n"
            f"🕒 Backup Date: <b>{ts} UTC</b>\n\n"
            f"<b>⚠️ ယခု DB ထဲမှာရှိတဲ့ data အကုန် overwrite ဖြစ်သွားမည်!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    except json.JSONDecodeError:
        await msg.edit_text("❌ JSON parse မရဘူး — file ပျက်နေနိုင်တယ်")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")


# ── Register handlers ──────────────────────────────────────────────────────────

application.add_handler(CommandHandler("backup",  backup_cmd,  block=False))
application.add_handler(CommandHandler("restore", restore_cmd, block=False))
application.add_handler(CallbackQueryHandler(backup_menu_callback, pattern=r"^backup:(do|restore_prompt)$",   block=False))
application.add_handler(CallbackQueryHandler(restore_callback,     pattern=r"^restore:(confirm|cancel)$",    block=False))
application.add_handler(MessageHandler(
    filters.Document.FileExtension("json") & filters.ChatType.PRIVATE,
    _auto_restore_detect,
    block=False,
))
