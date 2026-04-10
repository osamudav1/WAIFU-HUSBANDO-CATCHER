"""
modules/upload.py  —  Step-by-step upload via ConversationHandler.

Flow:
  /upload  OR  send photo directly in PM
    → bot saves photo file_id
    → ask character name
    → ask anime name
    → show rarity buttons
    → confirm & save directly to DB (no channel needed)

Other commands: /uploadchar /delete /update  (sudo only)
"""
import re

from pymongo import ReturnDocument
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackContext, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, filters,
)

from waifu import application, collection, db, sudo_users, OWNER_ID
from waifu.config import Config

RARITY_MAP  = Config.RARITY_MAP
RARITY_STRS = {v.lower(): v for v in RARITY_MAP.values()}

# Conversation states
WAIT_PHOTO, WAIT_NAME, WAIT_ANIME, WAIT_RARITY, WAIT_LIMIT = range(5)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_sudo(uid: int) -> bool:
    return uid in sudo_users or uid == OWNER_ID


_URL_RE = re.compile(
    r"https?://[^\s]+\.(?:jpg|jpeg|png|webp|gif)(?:\?[^\s]*)?",
    re.IGNORECASE,
)


def _get_photo_from_msg(msg) -> str | None:
    """Return file_id (for direct photos) or None."""
    if not msg:
        return None
    if msg.photo:
        return msg.photo[-1].file_id
    if msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        return msg.document.file_id
    return None


def _extract_url(msg) -> str | None:
    """Return image URL from a text message, or None."""
    if not msg or not msg.text:
        return None
    m = _URL_RE.search(msg.text.strip())
    return m.group(0) if m else None


async def _next_id() -> str:
    doc = await db.sequences.find_one_and_update(
        {"_id": "character_id"},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return str(doc["sequence_value"]).zfill(4)


def _rarity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚪ Common",            callback_data="rar:1"),
            InlineKeyboardButton("🟣 Rare",              callback_data="rar:2"),
        ],
        [
            InlineKeyboardButton("🟡 Legendary",         callback_data="rar:3"),
            InlineKeyboardButton("🔮 Mythical",          callback_data="rar:4"),
        ],
        [
            InlineKeyboardButton("💮 Special Edition",   callback_data="rar:5"),
            InlineKeyboardButton("🌌 Universal Limited", callback_data="rar:6"),
        ],
        [InlineKeyboardButton("❌ Cancel",               callback_data="rar:cancel")],
    ])


# ── Conversation steps ────────────────────────────────────────────────────────

async def upload_start(update: Update, context: CallbackContext) -> int:
    """/upload command — entry point."""
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "📸 <b>Upload — Step 1/4</b>\n\n"
        "Character ပုံ ၂ မျိုးပေးနိုင်တယ်:\n"
        "• ပုံ တိုက်ရိုက်ပို့ (ဓာတ်ပုံ / document)\n"
        "• jpg/png URL link paste လုပ်\n"
        "  <i>ဥပမာ: https://example.com/char.jpg</i>\n\n"
        "❌ ပယ်ဖျက်ရန် /cancel",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_PHOTO


async def step_photo(update: Update, context: CallbackContext) -> int:
    """Receive photo (direct file) or jpg/png URL link."""
    if not _is_sudo(update.effective_user.id):
        return ConversationHandler.END

    # Try direct photo/document first
    img = _get_photo_from_msg(update.message)

    # Fallback: URL link in text message
    if not img:
        img = _extract_url(update.message)

    if not img:
        await update.message.reply_text(
            "❌ ပုံ တိုက်ရိုက်ပို့ (သို့) jpg/png URL link ပေး\n\n"
            "<i>ဥပမာ URL: https://example.com/image.jpg</i>",
            parse_mode=ParseMode.HTML,
        )
        return WAIT_PHOTO

    context.user_data['photo'] = img
    src = "🔗 URL link" if img.startswith("http") else "📷 ပုံ"
    await update.message.reply_text(
        f"✅ <b>Step 2/4 — Character Name</b>  ({src})\n\n"
        "Character အမည် ရိုက်ပေး\n"
        "<i>Space ပါရင် dash (-) သုံး\n"
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
        f"✅ <b>Step 4/5 — Rarity</b>\n\n"
        f"Name: <b>{context.user_data['name']}</b>\n"
        f"Anime: <b>{anime}</b>\n\n"
        f"Rarity ရွေးပေး 👇",
        reply_markup=_rarity_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return WAIT_RARITY


async def step_rarity(update: Update, context: CallbackContext) -> int:
    """Receive rarity button → ask for limit."""
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

    if not all([context.user_data.get('photo'),
                context.user_data.get('name'),
                context.user_data.get('anime')]):
        await q.edit_message_text("❌ Session ကုန်သွားတယ်။ /upload ထပ်ကြိုးစား")
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data['rarity'] = rarity
    name  = context.user_data['name']
    anime = context.user_data['anime']

    await q.edit_message_text(
        f"✅ <b>Step 5/5 — Limit (copies)</b>\n\n"
        f"Name: <b>{name}</b>\n"
        f"Anime: <b>{anime}</b>\n"
        f"Rarity: <b>{rarity}</b>\n\n"
        f"🔢 Character ဘယ်နှစ်ကောင် claim လုပ်ခွင့်ပြုမလဲ?\n"
        f"<i>ကြိုက်သလောက် ဂဏန်း ရိုက်ထည့်ပေး (ဥပမာ: 5, 10, 50 ...)</i>\n\n"
        f"❌ ပယ်ဖျက်ရန် /cancel",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_LIMIT


async def step_limit(update: Update, context: CallbackContext) -> int:
    """Receive limit number → save to DB."""
    text = update.message.text.strip()

    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text(
            "❌ 1 အထက် ဂဏန်းတစ်ခု ရိုက်ထည့်ပေး (ဥပမာ: 10)")
        return WAIT_LIMIT

    limit = int(text)
    photo = context.user_data.get('photo')
    name  = context.user_data.get('name')
    anime = context.user_data.get('anime')
    rarity= context.user_data.get('rarity')

    if not all([photo, name, anime, rarity]):
        await update.message.reply_text("❌ Session ကုန်သွားတယ်။ /upload ထပ်ကြိုးစား")
        context.user_data.clear()
        return ConversationHandler.END

    # ── Auto-store photo in FILE_STORE_CHAT → get this bot's own file_id ─────
    # CachedPhoto in inline queries only works with THIS bot's file_ids.
    # Sending the photo through our bot gives us a permanent, bot-owned file_id.
    img_url   = photo
    bot_local = update.get_bot()
    store_chat = Config.FILE_STORE_CHAT_ID

    if not photo.startswith("http"):
        # Try to push through FILE_STORE_CHAT or directly via get_file
        # to obtain an HTTPS URL (works with InlineQueryResultPhoto everywhere)
        send_target = store_chat if store_chat else None
        try:
            if send_target:
                stored_msg = await bot_local.send_photo(chat_id=send_target, photo=photo)
                fid = stored_msg.photo[-1].file_id if stored_msg.photo else None
            else:
                fid = photo   # use original file_id for get_file call
            if fid:
                file_obj = await bot_local.get_file(fid)
                img_url  = file_obj.file_path      # full HTTPS URL
        except Exception as store_err:
            from waifu import LOGGER
            LOGGER.warning("img_url HTTPS conversion failed: %s", store_err)

    char_id = await _next_id()
    char = {
        "img_url":       img_url,
        "name":          name,
        "anime":         anime,
        "rarity":        rarity,
        "id":            char_id,
        "limit":         limit,
        "claimed_count": 0,
    }

    try:
        await collection.insert_one(char)
        src_label = "🔗 URL" if photo.startswith("http") else "📷 ပုံ (bot file_id)"
        await update.message.reply_text(
            f"🎉 <b>Upload ပြီးပြီ!</b>\n\n"
            f"🌸 <b>{name}</b>\n"
            f"📺 {anime}\n"
            f"💎 {rarity}\n"
            f"🔢 Limit: <b>{limit} copies</b>\n"
            f"🆔 ID: <code>{char_id}</code>\n"
            f"🖼 Source: {src_label}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ DB သိမ်းမရဘူး: {e}", parse_mode=ParseMode.HTML)

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Upload ပယ်ဖျက်လိုက်တယ်။")
    return ConversationHandler.END


# ── /migrateimgs — bulk-fix all character file_ids via FILE_STORE_CHAT ────────

async def migrate_imgs(update: Update, context: CallbackContext) -> None:
    """Owner only: re-send all non-URL character images through this bot to get
    bot-owned file_ids, then update the DB.  Required once for inline to work."""
    import asyncio as _asyncio
    uid = update.effective_user.id
    if uid != OWNER_ID and not _is_sudo(uid):
        await update.message.reply_text("❌ Owner/Sudo only.")
        return

    store_chat = Config.FILE_STORE_CHAT_ID
    if not store_chat:
        await update.message.reply_text("❌ FILE_STORE_CHAT_ID not configured.")
        return

    msg = await update.message.reply_text("⏳ Migrating character images…  (0/?)")

    all_chars = await collection.find({}).to_list(length=10_000)
    to_fix    = [c for c in all_chars if not c.get("img_url", "").startswith("http")]
    total     = len(to_fix)
    done = 0; skipped = 0

    for c in to_fix:
        try:
            sent = await context.bot.send_photo(
                chat_id=store_chat,
                photo=c["img_url"],
            )
            if sent.photo:
                fid      = sent.photo[-1].file_id
                file_obj = await context.bot.get_file(fid)
                new_url  = file_obj.file_path          # full HTTPS URL
                await collection.update_one(
                    {"id": c["id"]},
                    {"$set": {"img_url": new_url}},
                )
                done += 1
        except Exception:
            skipped += 1

        # Progress update every 10 chars; Telegram rate-limit safety
        if (done + skipped) % 10 == 0:
            try:
                await msg.edit_text(
                    f"⏳ Migrating…  {done + skipped}/{total}  "
                    f"(✅{done} ❌{skipped})"
                )
            except Exception:
                pass
        await _asyncio.sleep(0.4)      # ~2.5 sends/sec — well under Telegram limit

    await msg.edit_text(
        f"✅ Migration ပြီးပြီ!\n\n"
        f"✅ Updated: {done}\n"
        f"❌ Skipped: {skipped}\n"
        f"📦 Total:   {total}"
    )


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

    char = {
        "img_url": photo,
        "name":    parsed["name"],
        "anime":   parsed["anime"],
        "rarity":  parsed["rarity"],
        "id":      char_id,
    }

    try:
        await collection.insert_one(char)
        await update.message.reply_text(
            f"🎉 <b>{parsed['name']}</b> upload ပြီးပြီ!\n"
            f"💎 {parsed['rarity']}\n"
            f"📺 {parsed['anime']}\n"
            f"🆔 ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ DB သိမ်းမရဘူး: {e}",
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
    await update.message.reply_text(
        f"✅ <b>{char['name']}</b> (<code>{char['id']}</code>) ဖျက်ပြီ",
        parse_mode=ParseMode.HTML,
    )


# ── /update ───────────────────────────────────────────────────────────────────

_VALID = {"img_url", "name", "anime", "rarity", "limit", "claimed_count"}


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
    elif field in ("limit", "claimed_count"):
        if not raw.isdigit() or int(raw) < 0:
            await upd.message.reply_text("❌ ဂဏန်းသာ ထည့်ပေး (0 နဲ့ အထက်)")
            return
        new_val = int(raw)
    else:
        new_val = raw

    await collection.update_one({"id": char_id}, {"$set": {field: new_val}})
    await upd.message.reply_text(
        f"✅ <b>{char['name']}</b> — <code>{field}</code> update ပြီ",
        parse_mode=ParseMode.HTML,
    )


# ── Register handlers ─────────────────────────────────────────────────────────

_PHOTO_FILTER = filters.PHOTO | filters.Document.IMAGE
_PHOTO_OR_URL = _PHOTO_FILTER | (filters.TEXT & ~filters.COMMAND)

_upload_conv = ConversationHandler(
    entry_points=[
        CommandHandler("upload", upload_start),
        # Direct photo in PM starts the conversation (no accidental trigger on text)
        MessageHandler(_PHOTO_FILTER & filters.ChatType.PRIVATE, step_photo),
    ],
    states={
        # In WAIT_PHOTO: accept both photo AND URL text
        WAIT_PHOTO:  [MessageHandler(_PHOTO_OR_URL, step_photo)],
        WAIT_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_name)],
        WAIT_ANIME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, step_anime)],
        WAIT_RARITY: [CallbackQueryHandler(step_rarity, pattern=r"^rar:")],
        WAIT_LIMIT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, step_limit)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True,
    per_message=False,
)

application.add_handler(_upload_conv)
application.add_handler(CommandHandler("uploadchar",  uploadchar,   block=False))
application.add_handler(CommandHandler("delete",      delete,       block=False))
application.add_handler(CommandHandler("update",      update_char,  block=False))
application.add_handler(CommandHandler("migrateimgs", migrate_imgs, block=False))
