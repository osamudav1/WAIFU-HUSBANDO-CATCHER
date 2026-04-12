"""
modules/harem.py — Harem list view grouped by anime.

Layout:
  [Character photo]
  [Username]'s RECENT CHARACTERS — PAGE: X/Y
  ⚜️ Anime Name (owned/total)
  ┄┄┄┄┄┄┄┄┄
  🍀 ID | rarity | Name ×count
  ...
  [ ⬅️ PREV ]  [ NEXT ➡️ ]
  [ ⛩ CHARACTERS (N) ]
"""
import math
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, collection as waifu_collection

_CHARS_PER_PAGE = 10

_MEDALS = {
    "⚪ Common":            "⚪",
    "🟣 Rare":              "🟣",
    "🟤 Medium":            "🟤",
    "🟡 Legendary":         "🟡",
    "🔮 Mythical":          "🔮",
    "💮 Special Edition":   "💮",
    "🌐 Global":            "🌐",
    "🌌 Universal Limited": "🌌",
}


def _rarity_icon(rarity: str) -> str:
    return _MEDALS.get(rarity, "🎴")


async def _build_list_view(
    user_id: int,
    page: int,
    viewer_id: int | None = None,
) -> tuple[str, str | None, InlineKeyboardMarkup, int]:
    """
    Returns (caption, photo_file_id, keyboard, total_unique_chars).
    """
    user = await user_collection.find_one({"id": user_id})
    if not user or not user.get("characters"):
        owner_name = user.get("first_name", str(user_id)) if user else str(user_id)
        msg = (
            f"📭 <b>{escape(owner_name)}</b> ရဲ့ harem မှာ character မရှိသေးဘူး!"
            if viewer_id and viewer_id != user_id
            else "📭 Harem မှာ character မရှိသေးဘူး — character တစ်ကောင် ဖမ်းပေး!"
        )
        return msg, None, InlineKeyboardMarkup([]), 0

    chars      = user["characters"]
    owner_name = user.get("first_name", str(user_id))
    fav_id     = (user.get("favorites") or [None])[0]
    stars_map  = {}

    # Count duplicates
    id_counts: dict[str, int] = {}
    for c in chars:
        id_counts[c["id"]] = id_counts.get(c["id"], 0) + 1

    # Unique chars sorted by anime → id
    unique: list[dict] = list({c["id"]: c for c in chars}.values())
    unique.sort(key=lambda x: (x["anime"], x["id"]))

    total_chars = len(unique)
    total_pages = max(1, math.ceil(total_chars / _CHARS_PER_PAGE))
    page        = max(0, min(page, total_pages - 1))

    page_chars = unique[page * _CHARS_PER_PAGE : (page + 1) * _CHARS_PER_PAGE]

    # Photo = first PHOTO char on the page (skip video file_ids)
    photo = next(
        (c.get("img_url") for c in page_chars
         if c.get("img_url")
         and not c["img_url"].startswith("http")
         and c.get("media_type", "photo") != "video"),
        None,
    )

    # Group page chars by anime
    anime_groups: dict[str, list[dict]] = {}
    for c in page_chars:
        anime_groups.setdefault(c["anime"], []).append(c)

    # Fetch anime totals in bulk
    anime_list  = list(anime_groups.keys())
    anime_total = {}
    for anime in anime_list:
        anime_total[anime] = await waifu_collection.count_documents({"anime": anime})

    # Build caption lines
    mention = f"<a href='tg://user?id={user_id}'>{escape(owner_name)}</a>"
    header  = (
        f"📋 <b>{mention}'s RECENT CHARACTERS</b> "
        f"— PAGE: {page + 1}/{total_pages}\n\n"
    )

    lines: list[str] = []
    for anime, achars in anime_groups.items():
        user_anime_cnt = sum(1 for x in chars if x["anime"] == anime)
        db_total       = anime_total.get(anime, "?")
        lines.append(f"⚜️ <b>{escape(anime)}</b> ({user_anime_cnt}/{db_total})")
        lines.append("┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄")
        for c in achars:
            rar   = _rarity_icon(c.get("rarity", ""))
            cnt   = id_counts.get(c["id"], 1)
            fav   = " ⭐" if c["id"] == fav_id else ""
            g_rank = (f" 🌐<code>#{c['global_rank']}</code>"
                      if c.get("global_rank") else "")
            lines.append(
                f"🍀 <code>{c['id']}</code> | {rar} | {escape(c['name'])}{fav}{g_rank} ×{cnt}"
            )
        lines.append("")

    body = "\n".join(lines).strip()
    # Truncate body before wrapping (caption limit = 1024 for photos)
    max_body = 1024 - len(header) - len("<blockquote></blockquote>") - 5
    if len(body) > max_body:
        body = body[:max_body] + "…"
    caption = header + f"<blockquote>{body}</blockquote>"

    # Keyboard
    _vid = viewer_id if viewer_id else user_id
    nav  = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ PREV", callback_data=f"harem:{page - 1}:{user_id}:{_vid}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("NEXT ➡️", callback_data=f"harem:{page + 1}:{user_id}:{_vid}"))

    kb_rows: list[list] = []
    if nav:
        kb_rows.append(nav)
    kb_rows.append([
        InlineKeyboardButton(f"⛩ CHARACTERS ({total_chars})", callback_data="noop"),
    ])
    kb_rows.append([
        InlineKeyboardButton(
            "🔱 Harem Collection",
            switch_inline_query_current_chat=f"harem.{user_id}",
        ),
    ])

    markup = InlineKeyboardMarkup(kb_rows)
    return caption, photo, markup, total_chars


# ── /harem command ────────────────────────────────────────────────────────────

async def harem(update: Update, context: CallbackContext, page: int = 0) -> None:
    viewer_id = update.effective_user.id
    target_id = viewer_id

    if context.args:
        arg = context.args[0].strip()
        is_char_id = arg.startswith("0") or len(arg) < 7

        if is_char_id:
            # Jump to the page containing this char ID
            user_doc = await user_collection.find_one({"id": viewer_id})
            chars    = user_doc.get("characters", []) if user_doc else []
            unique   = list({c["id"]: c for c in chars}.values())
            unique.sort(key=lambda x: (x["anime"], x["id"]))
            char_idx = next(
                (i for i, c in enumerate(unique) if c["id"].lower() == arg.lower()),
                None,
            )
            if char_idx is None:
                await update.message.reply_text(
                    f"❌ Character ID <code>{arg}</code> ကို မင်းရဲ့ harem မှာ မတွေ့ဘူး",
                    parse_mode=ParseMode.HTML,
                )
                return
            page = char_idx // _CHARS_PER_PAGE

        elif arg.lstrip("-").isdigit():
            target_id = int(arg)

        else:
            await update.message.reply_text(
                "❌ Character ID (ဥပမာ: 0006) သို့မဟုတ် User ID ထည့်ပေး"
            )
            return

    caption, photo, markup, total = await _build_list_view(target_id, page, viewer_id=viewer_id)

    if total == 0:
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML)
        return

    if photo:
        await update.message.reply_photo(
            photo=photo,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )


# ── Navigation callback ───────────────────────────────────────────────────────

async def harem_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()

    parts     = q.data.split(":")
    page      = int(parts[1])
    uid       = int(parts[2])
    viewer_id = int(parts[3]) if len(parts) >= 4 else uid

    caption, photo, markup, total = await _build_list_view(uid, page, viewer_id=viewer_id)

    if total == 0:
        try:
            await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML)
        except Exception:
            await q.edit_message_text(caption, parse_mode=ParseMode.HTML)
        return

    if photo:
        try:
            await q.edit_message_media(
                media=InputMediaPhoto(
                    media=photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                ),
                reply_markup=markup,
            )
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            try:
                await q.edit_message_caption(
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
            except Exception:
                pass
    else:
        try:
            await q.edit_message_text(
                caption,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise


# ── send_harem_card (called from other modules) ───────────────────────────────

async def send_harem_card(user_id: int, query) -> None:
    caption, photo, markup, total = await _build_list_view(user_id, 0, viewer_id=user_id)

    if total == 0:
        await query.answer(caption, show_alert=True)
        return

    if photo:
        await query.message.reply_photo(
            photo=photo,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    else:
        await query.message.reply_text(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )


async def noop(update: Update, context: CallbackContext) -> None:
    await update.callback_query.answer()


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler(["harem", "collection", "waifus", "mywaifu"], harem, block=False))
application.add_handler(CallbackQueryHandler(harem_callback, pattern=r"^harem:\d+:\d+(:\d+)?$", block=False))
application.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$", block=False))
