"""
modules/upload.py

Upload methods:

1) Reply to any image + /upload character-name anime-name rarity_number
   (Recommended — image is taken from the replied message)

2) /upload file_id character-name anime-name rarity_number
   (Use file_id obtained from the file-store group)

3) /uploadchar  (reply to a channel post whose caption is in the format):
   🍀 Name: Sasha Braus
   🍋 Rarity: Legendary
   🌸 Anime: Attack On Titan
   🌱 ID: 26          ← optional; auto-generated if absent

File-store group:
   Set FILE_STORE_CHAT_ID env var.  Any photo posted in that group
   will get an automatic file_id reply from the bot.
"""
import re

import aiohttp
from pymongo import ReturnDocument
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import application, collection, db, sudo_users, OWNER_ID, CHARA_CHANNEL_ID
from waifu.config import Config

RARITY_MAP  = Config.RARITY_MAP
RARITY_STRS = {v.lower(): v for v in RARITY_MAP.values()}

WRONG_FORMAT = (
    "❌ Wrong format\n\n"
    "<b>နည်း ၁</b> — ဓာတ်ပုံကို reply လုပ်ပြီး:\n"
    "<code>/upload character-name anime-name rarity_number</code>\n\n"
    "<b>နည်း ၂</b> — channel post link ထဲက message id သုံးပြီး:\n"
    "<code>/upload 123 character-name anime-name rarity_number</code>\n\n"
    "<b>Rarity numbers:</b>\n"
    + "\n".join(f"  {k} → {v}" for k, v in RARITY_MAP.items())
)


def _is_sudo(uid: int) -> bool:
    return uid in sudo_users or uid == OWNER_ID


def _is_message_id(val: str) -> bool:
    """Short positive integer → treat as message_id from file store channel."""
    return val.isdigit() and len(val) <= 10


def _is_file_id(val: str) -> bool:
    """Telegram file_ids are long strings that don't start with http."""
    return not val.startswith("http") and not val.isdigit() and len(val) > 20


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


def _get_photo_from_msg(msg) -> str | None:
    if msg.photo:
        return msg.photo[-1].file_id
    if msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        return msg.document.file_id
    return None


# ── /upload ───────────────────────────────────────────────────────────────────

async def upload(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return

    replied = update.message.reply_to_message
    photo   = _get_photo_from_msg(replied) if replied else None

    # ── Mode 1: reply to image + 3 args (name, anime, rarity) ─────────────────
    if photo and len(context.args) == 3:
        raw_name, raw_anime, raw_rarity = context.args

    # ── Mode 2: 4 args (msg_id / file_id / url, name, anime, rarity) ───────────
    elif len(context.args) == 4:
        img_ref, raw_name, raw_anime, raw_rarity = context.args

        if _is_message_id(img_ref):
            # Fetch image from FILE_STORE channel using message_id
            store_chat = Config.FILE_STORE_CHAT_ID
            if not store_chat:
                await update.message.reply_text(
                    "❌ FILE_STORE_CHAT_ID မသတ်မှတ်ထားသေးဘူး။")
                return
            try:
                fwd = await context.bot.forward_message(
                    chat_id=update.effective_chat.id,
                    from_chat_id=store_chat,
                    message_id=int(img_ref),
                )
                photo = _get_photo_from_msg(fwd)
                if not photo:
                    await update.message.reply_text(
                        "❌ ထို message မှာ ဓာတ်ပုံမပါဘူး။")
                    return
                # Delete the forwarded preview
                try:
                    await fwd.delete()
                except Exception:
                    pass
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Message ရှာမတွေ့ဘူး: {e}")
                return

        elif _is_file_id(img_ref):
            photo = img_ref
        elif await _validate_url(img_ref):
            photo = img_ref
        else:
            await update.message.reply_text("❌ message id / file_id / URL မမှန်ဘူး။")
            return

    else:
        await update.message.reply_text(WRONG_FORMAT, parse_mode=ParseMode.HTML)
        return

    try:
        rarity = RARITY_MAP[int(raw_rarity)]
    except (KeyError, ValueError):
        await update.message.reply_text(
            f"❌ Invalid rarity number. Use 1–{len(RARITY_MAP)}.")
        return

    name    = raw_name.replace("-", " ").title()
    anime   = raw_anime.replace("-", " ").title()
    char_id = await _next_id()
    char    = {"img_url": photo, "name": name, "anime": anime,
               "rarity": rarity, "id": char_id}

    try:
        msg = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=photo,
            caption=_char_caption(char, update.effective_user.id, update.effective_user.first_name),
            parse_mode=ParseMode.HTML,
        )
        char["message_id"] = msg.message_id
        await collection.insert_one(char)
        await update.message.reply_text(
            f"✅ <b>{name}</b> added!\n"
            f"💎 {rarity}\n"
            f"📺 {anime}\n"
            f"🆔 ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Channel post failed: {e}\nCharacter was <b>not</b> saved.",
            parse_mode=ParseMode.HTML,
        )


# ── /uploadchar (reply to formatted post) ────────────────────────────────────

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
            "❌ Reply to a post that has the character's image and caption.\n\n"
            "<b>Expected caption format:</b>\n"
            "🍀 Name: Character Name\n"
            "🍋 Rarity: Legendary\n"
            "🌸 Anime: Anime Name\n"
            "🌱 ID: 26  <i>(optional)</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    photo = _get_photo_from_msg(replied)
    if not photo:
        await update.message.reply_text("❌ The replied message must contain an image.")
        return

    caption = replied.caption or replied.text or ""
    parsed  = _parse_caption(caption)
    if not parsed:
        await update.message.reply_text(
            "❌ Could not parse the caption. Make sure it contains at least "
            "<b>Name</b> and <b>Anime</b> fields.",
            parse_mode=ParseMode.HTML,
        )
        return

    if parsed["id"]:
        existing = await collection.find_one({"id": parsed["id"]})
        if existing:
            await update.message.reply_text(
                f"❌ ID <code>{parsed['id']}</code> already exists in the database.",
                parse_mode=ParseMode.HTML,
            )
            return
        char_id = parsed["id"]
    else:
        char_id = await _next_id()

    char = {
        "img_url": photo,
        "name":    parsed["name"],
        "anime":   parsed["anime"],
        "rarity":  parsed["rarity"],
        "id":      char_id,
    }

    try:
        msg = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=photo,
            caption=_char_caption(char, update.effective_user.id, update.effective_user.first_name),
            parse_mode=ParseMode.HTML,
        )
        char["message_id"] = msg.message_id
        await collection.insert_one(char)
        await update.message.reply_text(
            f"✅ <b>{parsed['name']}</b> uploaded!\n"
            f"💎 {parsed['rarity']}\n"
            f"📺 {parsed['anime']}\n"
            f"🆔 ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Channel post failed: {e}\nCharacter was <b>not</b> saved.",
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
        await update.message.reply_text("❌ Character not found.")
        return
    if char.get("message_id"):
        try:
            await context.bot.delete_message(CHARA_CHANNEL_ID, char["message_id"])
        except Exception:
            pass
    await update.message.reply_text(
        f"✅ Deleted <b>{char['name']}</b> (<code>{char['id']}</code>)",
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
        await upd.message.reply_text(f"❌ Invalid field. Choose: {', '.join(_VALID)}")
        return

    char = await collection.find_one({"id": char_id})
    if not char:
        await upd.message.reply_text("❌ Character not found.")
        return

    if field in ("name", "anime"):
        new_val = raw.replace("-", " ").title()
    elif field == "rarity":
        try:
            new_val = RARITY_MAP[int(raw)]
        except (KeyError, ValueError):
            await upd.message.reply_text(f"❌ Invalid rarity. Use 1–{len(RARITY_MAP)}.")
            return
    elif field == "img_url":
        if not _is_file_id(raw) and not await _validate_url(raw):
            await upd.message.reply_text("❌ Invalid or unreachable image URL/file_id.")
            return
        new_val = raw
    else:
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
        await upd.message.reply_text(f"⚠️ DB updated but channel sync failed: {e}")
        return

    await upd.message.reply_text(
        f"✅ <b>{char['name']}</b> — <code>{field}</code> updated.",
        parse_mode=ParseMode.HTML,
    )


# ── File-store group: auto file_id reply ─────────────────────────────────────

_FILE_STORE_CHAT = Config.FILE_STORE_CHAT_ID


async def filestore_photo(update: Update, context: CallbackContext) -> None:
    """In the file-store group, reply to any photo with its message_id and file_id."""
    if not _FILE_STORE_CHAT:
        return
    if update.effective_chat.id != _FILE_STORE_CHAT:
        return
    msg = update.message
    if not msg:
        return
    photo = _get_photo_from_msg(msg)
    if not photo:
        return
    msg_id = msg.message_id
    await msg.reply_text(
        f"🔢 <b>Message ID:</b> <code>{msg_id}</code>\n\n"
        f"📋 <b>File ID:</b>\n<code>{photo}</code>\n\n"
        f"<b>Upload (message id သုံး):</b>\n"
        f"<code>/upload {msg_id} Character-Name Anime-Name 1</code>",
        parse_mode=ParseMode.HTML,
    )


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler("upload",     upload,       block=False))
application.add_handler(CommandHandler("uploadchar", uploadchar,   block=False))
application.add_handler(CommandHandler("delete",     delete,       block=False))
application.add_handler(CommandHandler("update",     update_char,  block=False))
application.add_handler(MessageHandler(
    filters.PHOTO & filters.ChatType.GROUPS,
    filestore_photo,
    block=False,
))
