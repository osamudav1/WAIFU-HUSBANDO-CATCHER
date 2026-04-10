"""
modules/upload.py  —  Step-by-step upload via ConversationHandler.

Flow:
  /upload  OR  send photo directly in PM
    → bot saves photo
    → ask character name
    → ask anime name
    → show rarity buttons
    → confirm & upload to channel + DB

Other commands: /uploadchar /delete /update  (sudo only)
File-store group: auto file_id + message_id reply when photo posted.
"""
import re

import aiohttp
from pymongo import ReturnDocument
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackContext, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, filters,
)

from waifu import application, collection, db, sudo_users, OWNER_ID, CHARA_CHANNEL_ID
from waifu.config import Config

RARITY_MAP  = Config.RARITY_MAP
RARITY_STRS = {v.lower(): v for v in RARITY_MAP.values()}

# Conversation states
WAIT_PHOTO, WAIT_NAME, WAIT_ANIME, WAIT_RARITY = range(4)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_sudo(uid: int) -> bool:
    return uid in sudo_users or uid == OWNER_ID


def _get_photo_from_msg(msg) -> str | None:
    if not msg:
        return None
    if msg.photo:
        return msg.photo[-1].file_id
    if msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        return msg.document.file_id
    return None


async def _validate_url(url: str) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                return r.status < 400
    except Exception:
        return False


async def _next_id() -> str:
    doc = await db.sequences.find_one_and_update(
        {"_id": "character_id"},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return str(doc["sequence_value"]).zfill(4)


def _char_caption(char: dict, uploader_id: int, uploader_name: str) -> str:
    return (
        f"🍀 <b>Name:</b> {char['name']}\n"
        f"🍋 <b>Rarity:</b> {char['rarity']}\n"
        f"🌸 <b>Anime:</b> {char['anime']}\n"
        f"🌱 <b>ID:</b> {char['id']}\n\n"
        f"Added by <a href='tg://user?id={uploader_id}'>{uploader_name}</a>"
    )


def _rarity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚪ Common",           callback_data="rar:1"),
            InlineKeyboardButton("🟣 Rare",             callback_data="rar:2"),
        ],
        [
            InlineKeyboardButton("🟡 Legendary",        callback_data="rar:3"),
            InlineKeyboardButton("🔮 Mythical",         callback_data="rar:4"),
        ],
        [
            InlineKeyboardButton("💮 Special Edition",  callback_data="rar:5"),
            InlineKeyboardButton("🌌 Universal Limited",callback_data="rar:6"),
        ],
        [InlineKeyboardButton("❌ Cancel",              callback_data="rar:cancel")],
    ])


# ── Conversation steps ────────────────────────────────────────────────────────

async def upload_start(update: Update, context: CallbackContext) -> int:
    """/upload command — entry point."""
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_photo(
        photo="https://telegra.ph/file/b925c3985f0f325e62e17.jpg",
        caption=(
            "📸 <b>Upload — Step 1/4</b>\n\n"
            "Character ပုံ ပို့ပေး\n\n"
            "❌ ပယ်ဖျက်ရန် /cancel"
        ),
        parse_mode=ParseMode.HTML,
    )
    return WAIT_PHOTO


async def step_photo(update: Update, context: CallbackContext) -> int:
    """Receive photo (in conversation or direct PM send)."""
    if not _is_sudo(update.effective_user.id):
        return ConversationHandler.END

    photo = _get_photo_from_msg(update.message)
    if not photo:
        await update.message.reply_text("❌ ပုံပဲ ပို့ပေး၊ တခြား file မရဘူး")
        return WAIT_PHOTO

    context.user_data['photo'] = photo

    await update.message.reply_text(
        "✅ <b>Step 2/4 — Character Name</b>\n\n"
        "Character အမည် ရိုက်ပေး\n"
        "<i>Space ပါရင် dash (‑) သုံး\n"
        "ဥပမာ: Monkey-D-Luffy</i>\n\n"
        "❌ ပယ်ဖျက်ရန် /cancel",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_NAME


async def step_name(update: Update, context: CallbackContext) -> int:
    """Receive character name."""
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ အမည် ထည့်ပေး")
        return WAIT_NAME

    name = text.replace("-", " ").title()
    context.user_data['name'] = name

    await update.message.reply_text(
        f"✅ <b>Step 3/4 — Anime Name</b>\n\n"
        f"Name: <b>{name}</b>\n\n"
        f"Anime အမည် ရိုက်ပေး\n"
        f"<i>ဥပမာ: One-Piece</i>\n\n"
        f"❌ ပယ်ဖျက်ရန် /cancel",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_ANIME


async def step_anime(update: Update, context: CallbackContext) -> int:
    """Receive anime name → show rarity buttons."""
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Anime အမည် ထည့်ပေး")
        return WAIT_ANIME

    anime = text.replace("-", " ").title()
    context.user_data['anime'] = anime

    await update.message.reply_text(
        f"✅ <b>Step 4/4 — Rarity</b>\n\n"
        f"Name: <b>{context.user_data['name']}</b>\n"
        f"Anime: <b>{anime}</b>\n\n"
        f"Rarity ရွေးပေး 👇",
        reply_markup=_rarity_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return WAIT_RARITY


async def step_rarity(update: Update, context: CallbackContext) -> int:
    """Receive rarity button → upload."""
    q = update.callback_query
    await q.answer()

    if q.data == "rar:cancel":
        context.user_data.clear()
        await q.edit_message_text("❌ Upload ပယ်ဖျက်လိုက်တယ်။")
        return ConversationHandler.END

    try:
        rarity_num = int(q.data.split(":")[1])
        rarity = RARITY_MAP[rarity_num]
    except (IndexError, KeyError, ValueError):
        await q.answer("❌ မမှန်ဘူး", show_alert=True)
        return WAIT_RARITY

    photo   = context.user_data.get('photo')
    name    = context.user_data.get('name')
    anime   = context.user_data.get('anime')

    if not all([photo, name, anime]):
        await q.edit_message_text("❌ Session ကုန်သွားတယ်။ /upload ထပ်ကြိုးစား")
        context.user_data.clear()
        return ConversationHandler.END

    await q.edit_message_text("⏳ Uploading...")

    char_id = await _next_id()
    char    = {"img_url": photo, "name": name, "anime": anime,
               "rarity": rarity, "id": char_id}

    try:
        msg = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=photo,
            caption=_char_caption(char, q.from_user.id, q.from_user.first_name),
            parse_mode=ParseMode.HTML,
        )
        char["message_id"] = msg.message_id
        await collection.insert_one(char)
        await q.edit_message_text(
            f"🎉 <b>Upload ပြီးပြီ!</b>\n\n"
            f"🌸 <b>{name}</b>\n"
            f"📺 {anime}\n"
            f"💎 {rarity}\n"
            f"🆔 ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await q.edit_message_text(
            f"❌ Channel post failed: {e}\n\nCharacter <b>not</b> saved.",
            parse_mode=ParseMode.HTML,
        )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Upload ပယ်ဖျက်လိုက်တယ်။")
    return ConversationHandler.END


# ── /uploadchar (reply to formatted post) ─────────────────────────────────────

def _parse_caption(caption: str) -> dict | None:
    fields: dict[str, str] = {}
    patterns = {
        "name":   r"(?:🍀\s*)?Name\s*:\s*(.+)",
        "rarity": r"(?:🍋\s*)?Rarity\s*:\s*(.+)",
        "anime":  r"(?:🌸\s*)?Anime\s*:\s*(.+)",
        "id":     r"(?:🌱\s*)?ID\s*:\s*(\S+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, caption, re.IGNORECASE)
        if m:
            fields[key] = m.group(1).strip()

    if "name" not in fields or "anime" not in fields:
        return None

    raw_rarity = fields.get("rarity", "").lower()
    rarity = RARITY_STRS.get(raw_rarity)
    if not rarity:
        for key, val in RARITY_STRS.items():
            if raw_rarity in key or key in raw_rarity:
                rarity = val
                break
    if not rarity:
        rarity = "⚪ Common"

    return {
        "name":   fields["name"].title(),
        "anime":  fields["anime"].title(),
        "rarity": rarity,
        "id":     fields.get("id"),
    }


async def uploadchar(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return

    replied = update.message.reply_to_message
    if not replied:
        await update.message.reply_text(
            "❌ Character ပုံ + caption ပါတဲ့ post ကို reply လုပ်ပြီး /uploadchar ရိုက်ပေး\n\n"
            "<b>Caption format:</b>\n"
            "🍀 Name: Character Name\n"
            "🍋 Rarity: Legendary\n"
            "🌸 Anime: Anime Name\n"
            "🌱 ID: 26  <i>(optional)</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    photo = _get_photo_from_msg(replied)
    if not photo:
        await update.message.reply_text("❌ Reply လုပ်တဲ့ message မှာ ပုံမပါဘူး")
        return

    caption = replied.caption or replied.text or ""
    parsed  = _parse_caption(caption)
    if not parsed:
        await update.message.reply_text(
            "❌ Caption parse မရဘူး — Name နဲ့ Anime ပါဖို့လိုတယ်",
            parse_mode=ParseMode.HTML,
        )
        return

    if parsed["id"]:
        existing = await collection.find_one({"id": parsed["id"]})
        if existing:
            await update.message.reply_text(
                f"❌ ID <code>{parsed['id']}</code> DB မှာ ရှိပြီးသား",
                parse_mode=ParseMode.HTML,
            )
            return
        char_id = parsed["id"]
    else:
        char_id = await _next_id()

    char = {"img_url": photo, "name": parsed["name"], "anime": parsed["anime"],
            "rarity": parsed["rarity"], "id": char_id}

    try:
        msg = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID, photo=photo,
            caption=_char_caption(char, update.effective_user.id, update.effective_user.first_name),
            parse_mode=ParseMode.HTML,
        )
        char["message_id"] = msg.message_id
        await collection.insert_one(char)
        await update.message.reply_text(
            f"🎉 <b>{parsed['name']}</b> upload ပြီးပြီ!\n"
            f"💎 {parsed['rarity']}\n📺 {parsed['anime']}\n"
            f"🆔 ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Channel post failed: {e}\nCharacter <b>not</b> saved.",
            parse_mode=ParseMode.HTML,
        )


# ── /delete ───────────────────────────────────────────────────────────────────

async def delete(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/delete ID</code>", parse_mode=ParseMode.HTML)
        return

    char = await collection.find_one_and_delete({"id": context.args[0]})
    if not char:
        await update.message.reply_text("❌ Character မတွေ့ဘူး")
        return
    if char.get("message_id"):
        try:
            await context.bot.delete_message(CHARA_CHANNEL_ID, char["message_id"])
        except Exception:
            pass
    await update.message.reply_text(
        f"✅ <b>{char['name']}</b> (<code>{char['id']}</code>) ဖျက်ပြီ",
        parse_mode=ParseMode.HTML,
    )


# ── /update ───────────────────────────────────────────────────────────────────

_VALID = {"img_url", "name", "anime", "rarity"}


async def update_char(upd: Update, context: CallbackContext) -> None:
    if not _is_sudo(upd.effective_user.id):
        await upd.message.reply_text("❌ Sudo only.")
        return
    if len(context.args) != 3:
        await upd.message.reply_text(
            "Usage: <code>/update ID field new_value</code>\n"
            f"Fields: {', '.join(_VALID)}",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id, field, raw = context.args
    if field not in _VALID:
        await upd.message.reply_text(f"❌ Field မမှန်ဘူး — {', '.join(_VALID)} ထဲကရွေး")
        return

    char = await collection.find_one({"id": char_id})
    if not char:
        await upd.message.reply_text("❌ Character မတွေ့ဘူး")
        return

    if field in ("name", "anime"):
        new_val = raw.replace("-", " ").title()
    elif field == "rarity":
        try:
            new_val = RARITY_MAP[int(raw)]
        except (KeyError, ValueError):
            await upd.message.reply_text(f"❌ Rarity မမှန်ဘူး — 1–{len(RARITY_MAP)} သုံး")
            return
    else:
        if not raw.startswith("http") and not await _validate_url(raw):
            pass  # accept file_ids too
        new_val = raw

    await collection.update_one({"id": char_id}, {"$set": {field: new_val}})
    char[field] = new_val

    try:
        if field == "img_url":
            if char.get("message_id"):
                await upd.message.bot.delete_message(CHARA_CHANNEL_ID, char["message_id"])
            msg = await upd.message.bot.send_photo(
                CHARA_CHANNEL_ID, photo=new_val,
                caption=_char_caption(char, upd.effective_user.id, upd.effective_user.first_name),
                parse_mode=ParseMode.HTML,
            )
            await collection.update_one({"id": char_id}, {"$set": {"message_id": msg.message_id}})
        elif char.get("message_id"):
            await upd.message.bot.edit_message_caption(
                CHARA_CHANNEL_ID, char["message_id"],
                caption=_char_caption(char, upd.effective_user.id, upd.effective_user.first_name),
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        await upd.message.reply_text(f"⚠️ DB ကို update ပြီးပေမဲ့ channel sync မရဘူး: {e}")
        return

    await upd.message.reply_text(
        f"✅ <b>{char['name']}</b> — <code>{field}</code> update ပြီ",
        parse_mode=ParseMode.HTML,
    )


# ── File-store group: auto file_id + message_id reply ────────────────────────

_FILE_STORE_CHAT = Config.FILE_STORE_CHAT_ID


async def filestore_photo(update: Update, context: CallbackContext) -> None:
    if not _FILE_STORE_CHAT:
        return
    if update.effective_chat.id != _FILE_STORE_CHAT:
        return
    msg   = update.message
    photo = _get_photo_from_msg(msg)
    if not photo:
        return
    msg_id = msg.message_id
    await msg.reply_text(
        f"🔢 <b>Message ID:</b> <code>{msg_id}</code>\n\n"
        f"📋 <b>File ID:</b>\n<code>{photo}</code>",
        parse_mode=ParseMode.HTML,
    )


# ── Register handlers ─────────────────────────────────────────────────────────

_upload_conv = ConversationHandler(
    entry_points=[
        CommandHandler("upload", upload_start),
        MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, step_photo),
    ],
    states={
        WAIT_PHOTO: [MessageHandler(filters.PHOTO, step_photo)],
        WAIT_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, step_name)],
        WAIT_ANIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_anime)],
        WAIT_RARITY:[CallbackQueryHandler(step_rarity, pattern=r"^rar:")],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True,
    per_message=False,
)

application.add_handler(_upload_conv)
application.add_handler(CommandHandler("uploadchar", uploadchar,  block=False))
application.add_handler(CommandHandler("delete",     delete,      block=False))
application.add_handler(CommandHandler("update",     update_char, block=False))
application.add_handler(MessageHandler(
    filters.PHOTO & filters.ChatType.GROUPS,
    filestore_photo,
    block=False,
))
