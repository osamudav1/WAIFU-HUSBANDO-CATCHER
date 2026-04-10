"""
modules/harem.py — Card-view harem: one character photo + info per page.

Navigation:  ⬅️  [n / total]  ➡️
Each card shows the character's photo with caption containing all info.
"""
import math
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, collection as waifu_collection

_MEDALS = {
    "⚪ Common":            "⚪",
    "🟣 Rare":              "🟣",
    "🟡 Legendary":         "🟡",
    "🔮 Mythical":          "🔮",
    "💮 Special Edition":   "💮",
    "🌌 Universal Limited": "🌌",
}


def _rarity_icon(rarity: str) -> str:
    return _MEDALS.get(rarity, "🎴")


async def _get_anime_total(anime: str) -> int:
    return await waifu_collection.count_documents({"anime": anime})


async def _build_card(user_id: int, idx: int) -> tuple[str, InlineKeyboardMarkup, str | None, int]:
    """
    Returns (caption, keyboard, photo_file_id, total_chars).
    idx is the 0-based position in the user's unique character list.
    """
    user = await user_collection.find_one({"id": user_id})
    if not user or not user.get("characters"):
        return (
            "📭 Harem မှာ character မရှိသေးဘူး — character တစ်ကောင် ဖမ်းပေး!",
            InlineKeyboardMarkup([]),
            None,
            0,
        )

    chars      = user["characters"]
    fav_id     = (user.get("favorites") or [None])[0]

    # Deduplicate — keep all occurrences for count
    id_counts: dict[str, int] = {}
    for c in chars:
        id_counts[c["id"]] = id_counts.get(c["id"], 0) + 1

    unique: list[dict] = list({c["id"]: c for c in chars}.values())
    unique.sort(key=lambda x: (x["anime"], x["id"]))

    total = len(unique)
    if total == 0:
        return (
            "📭 Harem မှာ character မရှိသေးဘူး!",
            InlineKeyboardMarkup([]),
            None,
            0,
        )

    idx = max(0, min(idx, total - 1))
    c   = unique[idx]

    # Stats
    cnt       = id_counts.get(c["id"], 1)
    dup_line  = f"  ×{cnt} copies" if cnt > 1 else ""
    fav_mark  = " ⭐" if c["id"] == fav_id else ""
    rar_icon  = _rarity_icon(c.get("rarity", ""))
    anime_tot = await _get_anime_total(c["anime"])
    user_anime_cnt = sum(
        1 for x in chars if x["anime"] == c["anime"]
    )

    caption = (
        f"🌸 <b>{escape(c['name'])}</b>{fav_mark}{dup_line}\n\n"
        f"📺 Aɴɪᴍᴇ: {escape(c['anime'])}  ({user_anime_cnt}/{anime_tot})\n"
        f"{rar_icon} Rᴀʀɪᴛʏ: {c.get('rarity', '?')}\n"
        f"🆔 ID: <code>{c['id']}</code>\n\n"
        f"📦 {total} characters  |  💰 {user.get('coins', 0):,} coins"
    )

    # Navigation keyboard
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"harem:{idx-1}:{user_id}"))
    nav.append(InlineKeyboardButton(f"{idx+1} / {total}", callback_data="noop"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"harem:{idx+1}:{user_id}"))

    kb = [
        nav,
        [InlineKeyboardButton(
            "🔍 Search Collection",
            switch_inline_query_current_chat=f"collection.{user_id}",
        )],
    ]
    markup   = InlineKeyboardMarkup(kb)
    photo_id = c.get("img_url")

    return caption, markup, photo_id, total


# ── /harem command ────────────────────────────────────────────────────────────

async def harem(update: Update, context: CallbackContext, idx: int = 0) -> None:
    user_id           = update.effective_user.id
    caption, markup, photo, total = await _build_card(user_id, idx)

    if total == 0:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(caption, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(caption, parse_mode=ParseMode.HTML)
        return

    if update.callback_query:
        return  # handled by harem_callback

    # Command invocation — send new photo card
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


# ── Inline / callback navigation ──────────────────────────────────────────────

async def harem_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")
    idx      = int(parts[1])
    uid      = int(parts[2])

    if q.from_user.id != uid:
        await q.answer("❌ မင်းရဲ့ harem မဟုတ်ဘူး!", show_alert=True)
        return

    caption, markup, photo, total = await _build_card(uid, idx)

    if total == 0:
        try:
            await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML)
        except Exception:
            await q.edit_message_text(caption, parse_mode=ParseMode.HTML)
        return

    if photo:
        try:
            # Try to update photo + caption together
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
            # Fallback: just update caption if media edit fails
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


# ── Quick harem for action_callback (from start/guess buttons) ────────────────

async def send_harem_card(user_id: int, query) -> None:
    """Send harem as a new photo card (used from other modules' callbacks)."""
    caption, markup, photo, total = await _build_card(user_id, 0)

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

application.add_handler(CommandHandler(["harem", "collection"], harem, block=False))
application.add_handler(CallbackQueryHandler(harem_callback, pattern=r"^harem:\d+:\d+$", block=False))
application.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$", block=False))
