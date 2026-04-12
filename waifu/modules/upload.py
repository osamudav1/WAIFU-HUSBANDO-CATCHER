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
from html import escape

from pymongo import ReturnDocument
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackContext, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, filters,
)

from waifu import application, collection, user_collection, db, sudo_users, OWNER_ID, CHARA_CHANNEL_ID
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


def _get_media_from_msg(msg) -> tuple[str, str] | tuple[None, None]:
    """Return (file_id, media_type) where media_type is 'photo' or 'video', or (None, None)."""
    if not msg:
        return None, None
    if msg.photo:
        return msg.photo[-1].file_id, "photo"
    if msg.video:
        return msg.video.file_id, "video"
    if msg.animation:
        return msg.animation.file_id, "video"
    if msg.document and msg.document.mime_type:
        mt = msg.document.mime_type
        if mt.startswith("image/"):
            return msg.document.file_id, "photo"
        if mt.startswith("video/"):
            return msg.document.file_id, "video"
    return None, None


def _get_photo_from_msg(msg) -> str | None:
    fid, _ = _get_media_from_msg(msg)
    return fid


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
            InlineKeyboardButton("🟤 Medium",            callback_data="rar:3"),
        ],
        [
            InlineKeyboardButton("🟡 Legendary",         callback_data="rar:4"),
            InlineKeyboardButton("🔮 Mythical",          callback_data="rar:5"),
        ],
        [
            InlineKeyboardButton("💮 Special Edition",   callback_data="rar:6"),
            InlineKeyboardButton("🌐 Global",            callback_data="rar:7"),
        ],
        [
            InlineKeyboardButton("🌌 Universal Limited", callback_data="rar:8"),
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
        "Character ပုံ <b>သို့မဟုတ်</b> 🎬 <b>Video (AMV)</b> ပို့နိုင်တယ်:\n"
        "• 📷 Photo တိုက်ရိုက်ပို့\n"
        "• 🎬 Video / GIF / Animation တိုက်ရိုက်ပို့\n"
        "• jpg/png URL link paste လုပ်\n\n"
        "❌ ပယ်ဖျက်ရန် /cancel",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_PHOTO


async def step_photo(update: Update, context: CallbackContext) -> int:
    """Receive photo/video (direct file) or jpg/png URL link."""
    if not _is_sudo(update.effective_user.id):
        return ConversationHandler.END

    # Try direct photo/video/document first
    img, media_type = _get_media_from_msg(update.message)

    # Fallback: URL link in text message (photo only)
    if not img:
        img = _extract_url(update.message)
        if img:
            media_type = "photo"

    if not img:
        await update.message.reply_text(
            "❌ ပုံ သို့ Video တိုက်ရိုက်ပို့ (သို့) jpg/png URL link ပေး",
            parse_mode=ParseMode.HTML,
        )
        return WAIT_PHOTO

    context.user_data['photo']      = img
    context.user_data['media_type'] = media_type
    if media_type == "video":
        src = "🎬 Video/AMV"
    elif img.startswith("http"):
        src = "🔗 URL link"
    else:
        src = "📷 ပုံ"

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

    limit      = int(text)
    photo      = context.user_data.get('photo')
    name       = context.user_data.get('name')
    anime      = context.user_data.get('anime')
    rarity     = context.user_data.get('rarity')
    media_type = context.user_data.get('media_type', 'photo')

    if not all([photo, name, anime, rarity]):
        await update.message.reply_text("❌ Session ကုန်သွားတယ်။ /upload ထပ်ကြိုးစား")
        context.user_data.clear()
        return ConversationHandler.END

    # ── Auto-store in FILE_STORE_CHAT → get this bot's own file_id ───────────
    img_url   = photo
    bot_local = update.get_bot()
    store_chat = Config.FILE_STORE_CHAT_ID

    if not photo.startswith("http") and store_chat:
        try:
            if media_type == "video":
                stored_msg = await bot_local.send_video(chat_id=store_chat, video=photo)
                if stored_msg.video:
                    img_url = stored_msg.video.file_id
            else:
                stored_msg = await bot_local.send_photo(chat_id=store_chat, photo=photo)
                if stored_msg.photo:
                    img_url = stored_msg.photo[-1].file_id
        except Exception as store_err:
            from waifu import LOGGER
            LOGGER.warning("FILE_STORE push failed, using original file_id: %s", store_err)

    char_id = await _next_id()
    char = {
        "img_url":       img_url,
        "media_type":    media_type,
        "name":          name,
        "anime":         anime,
        "rarity":        rarity,
        "id":            char_id,
        "limit":         limit,
        "claimed_count": 0,
    }

    try:
        await collection.insert_one(char)
        await update.message.reply_text(
            f"🎉 <b>Upload ပြီးပြီ!</b>\n\n"
            f"🌸 <b>{name}</b>\n"
            f"📺 {anime}\n"
            f"💎 {rarity}\n"
            f"🔢 Limit: <b>{limit} copies</b>\n"
            f"🆔 ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )

        # ── Notify CHARA_CHANNEL_ID ───────────────────────────────────────────
        if CHARA_CHANNEL_ID:
            u = update.effective_user
            mention  = f'<a href="tg://user?id={u.id}">{escape(u.first_name)}</a>'
            chan_cap = f"•{mention}• uploaded <b>{escape(name)}</b>"
            try:
                if media_type == "video":
                    await bot_local.send_video(
                        chat_id=CHARA_CHANNEL_ID,
                        video=img_url,
                        caption=chan_cap,
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await bot_local.send_photo(
                        chat_id=CHARA_CHANNEL_ID,
                        photo=img_url,
                        caption=chan_cap,
                        parse_mode=ParseMode.HTML,
                    )
            except Exception as ch_err:
                from waifu import LOGGER
                LOGGER.warning("Channel notify failed: %s", ch_err)

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

    import httpx as _httpx
    all_chars = await collection.find({}).to_list(length=10_000)

    # Fix both: old-bot file_ids AND api.telegram.org URLs (expired CDN links)
    def _needs_fix(img: str) -> bool:
        return not img.startswith("http") or "api.telegram.org" in img

    to_fix = [c for c in all_chars if _needs_fix(c.get("img_url", ""))]
    total  = len(to_fix)
    done = 0; skipped = 0

    async with _httpx.AsyncClient(timeout=20) as http:
        for c in to_fix:
            img = c.get("img_url", "")
            try:
                if "api.telegram.org" in img:
                    # Expired CDN URL — download bytes ourselves, then re-upload
                    resp = await http.get(img)
                    resp.raise_for_status()
                    photo_data = resp.content
                else:
                    photo_data = img   # regular file_id

                sent = await context.bot.send_photo(
                    chat_id=store_chat,
                    photo=photo_data,
                )
                if sent.photo:
                    new_fid = sent.photo[-1].file_id   # permanent file_id
                    await collection.update_one(
                        {"id": c["id"]},
                        {"$set": {"img_url": new_fid}},
                    )
                    done += 1
                else:
                    skipped += 1
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

    char_id = context.args[0]

    # Remove from main character collection
    char = await collection.find_one_and_delete({"id": char_id})
    if not char:
        await update.message.reply_text("❌ Character မတွေ့ဘူး")
        return

    # Count how many users have this character before removing
    affected_users = await user_collection.count_documents(
        {"characters": {"$elemMatch": {"id": char_id}}}
    )

    # Remove character from ALL users' harem arrays
    pull_result = await user_collection.update_many(
        {"characters.id": char_id},
        {"$pull": {"characters": {"id": char_id}}},
    )

    await update.message.reply_text(
        f"✅ <b>{escape(char['name'])}</b> (<code>{char['id']}</code>) ဖျက်ပြီ\n"
        f"👥 {affected_users} ဦးရဲ့ harem ကနေ ထုတ်လိုက်ပြီ",
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


# ── /charactervdadd — attach a video clip (AMV) to an existing character ───────

async def charactervdadd(update: Update, context: CallbackContext) -> None:
    """
    Owner only. Reply to a video message with /charactervdadd <char_id>
    to attach that video as the AMV clip for the character (shown in /check).
    """
    uid = update.effective_user.id
    if uid != OWNER_ID and not _is_sudo(uid):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: video message ကို reply ဆွဲပြီး\n"
            "<code>/charactervdadd &lt;char_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id = context.args[0].strip()

    # Must be replying to a video / animation / document-video
    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text("❌ Video message ကို reply ဆွဲပြီး ရိုက်ပေး။")
        return

    file_id = None
    if reply.video:
        file_id = reply.video.file_id
    elif reply.animation:
        file_id = reply.animation.file_id
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video/"):
        file_id = reply.document.file_id

    if not file_id:
        await update.message.reply_text("❌ Reply message မှာ video မတွေ့ဘူး။")
        return

    char = await collection.find_one({"id": char_id})
    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{escape(char_id)}</code> မတွေ့ဘူး။",
            parse_mode=ParseMode.HTML,
        )
        return

    await collection.update_one(
        {"id": char_id},
        {"$set": {"video_url": file_id}},
    )

    await update.message.reply_text(
        f"✅ <b>{escape(char.get('name','?'))}</b> (<code>{char_id}</code>) ကို\n"
        f"🎬 AMV video တပ်ပြီးပြီ! /check {char_id} နဲ့ စစ်ကြည့်ပေး။",
        parse_mode=ParseMode.HTML,
    )


# ── /deletevd — remove the video clip from a character ───────────────────────

async def deletevd(update: Update, context: CallbackContext) -> None:
    """Owner only. /deletevd <char_id> removes the video_url field."""
    uid = update.effective_user.id
    if uid != OWNER_ID and not _is_sudo(uid):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/deletevd &lt;char_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id = context.args[0].strip()
    char = await collection.find_one({"id": char_id})
    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{escape(char_id)}</code> မတွေ့ဘူး။",
            parse_mode=ParseMode.HTML,
        )
        return

    if not char.get("video_url"):
        await update.message.reply_text(
            f"⚠️ <b>{escape(char.get('name','?'))}</b> မှာ video မရှိဘူး။",
            parse_mode=ParseMode.HTML,
        )
        return

    await collection.update_one(
        {"id": char_id},
        {"$unset": {"video_url": ""}},
    )

    await update.message.reply_text(
        f"🗑 <b>{escape(char.get('name','?'))}</b> (<code>{char_id}</code>) ရဲ့\n"
        f"AMV video ဖျက်ပြီးပြီ။",
        parse_mode=ParseMode.HTML,
    )


# ── Register handlers ─────────────────────────────────────────────────────────

_PHOTO_FILTER  = filters.PHOTO | filters.Document.IMAGE
_VIDEO_FILTER  = filters.VIDEO | filters.ANIMATION | filters.Document.VIDEO
_MEDIA_FILTER  = _PHOTO_FILTER | _VIDEO_FILTER
_PHOTO_OR_URL  = _MEDIA_FILTER | (filters.TEXT & ~filters.COMMAND)

_upload_conv = ConversationHandler(
    entry_points=[
        CommandHandler("upload", upload_start),
        # Direct photo/video in PM starts the conversation
        MessageHandler(_MEDIA_FILTER & filters.ChatType.PRIVATE, step_photo),
    ],
    states={
        # In WAIT_PHOTO: accept photo, video, OR URL text
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
application.add_handler(CommandHandler("uploadchar",      uploadchar,      block=False))
application.add_handler(CommandHandler("delete",          delete,          block=False))
application.add_handler(CommandHandler("update",          update_char,     block=False))
application.add_handler(CommandHandler("migrateimgs",     migrate_imgs,    block=False))
application.add_handler(CommandHandler("charactervdadd",  charactervdadd,  block=False))
application.add_handler(CommandHandler("deletevd",        deletevd,        block=False))
