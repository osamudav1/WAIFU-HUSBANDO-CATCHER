"""
modules/harem.py — Harem list view with /hmode support.

Modes   : default (grouped by anime) | detailed (per-character with full info)
Sort    : anime (default) | rarity (highest first)
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
    "🪞 Supreme":           "🪞",
    "💮 Special Edition":   "💮",
    "🌐 Global":            "🌐",
    "🌌 Universal":         "🌌",
}

_RARITY_ORDER = {
    "🌌 Universal":         9,
    "🌐 Global":            8,
    "💮 Special Edition":   7,
    "🪞 Supreme":           6,
    "🔮 Mythical":          5,
    "🟡 Legendary":         4,
    "🟤 Medium":            3,
    "🟣 Rare":              2,
    "⚪ Common":            1,
}


def _rarity_icon(rarity: str) -> str:
    return _MEDALS.get(rarity, "🎴")


async def _get_prefs(user_id: int) -> tuple[str, str]:
    doc = await user_collection.find_one({"id": user_id}, {"harem_mode": 1, "harem_sort": 1})
    mode = (doc or {}).get("harem_mode", "default")
    sort = (doc or {}).get("harem_sort", "anime")
    return mode, sort


async def _build_list_view(
    user_id: int,
    page: int,
    viewer_id: int | None = None,
    mode: str = "default",
    sort: str = "anime",
) -> tuple[str, str | None, InlineKeyboardMarkup, int]:
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

    id_counts: dict[str, int] = {}
    for c in chars:
        id_counts[c["id"]] = id_counts.get(c["id"], 0) + 1

    unique: list[dict] = list({c["id"]: c for c in chars}.values())

    if sort == "rarity":
        unique.sort(key=lambda x: (-_RARITY_ORDER.get(x.get("rarity", ""), 0), x["id"]))
    else:
        unique.sort(key=lambda x: (x["anime"], x["id"]))

    total_chars = len(unique)
    total_pages = max(1, math.ceil(total_chars / _CHARS_PER_PAGE))
    page        = max(0, min(page, total_pages - 1))
    page_chars  = unique[page * _CHARS_PER_PAGE : (page + 1) * _CHARS_PER_PAGE]

    photo = next(
        (c.get("img_url") for c in page_chars
         if c.get("img_url")
         and not c["img_url"].startswith("http")
         and c.get("media_type", "photo") != "video"),
        None,
    )

    mention = f"<a href='tg://user?id={user_id}'>{escape(owner_name)}</a>"
    header  = (
        f"📋 <b>{mention}'s RECENT CHARACTERS</b> "
        f"— PAGE: {page + 1}/{total_pages}\n\n"
    )

    if mode == "detailed":
        body = _build_detailed_body(page_chars, id_counts, fav_id)
    else:
        body = await _build_default_body(page_chars, id_counts, fav_id, chars)

    max_body = 1024 - len(header) - len("<blockquote></blockquote>") - 5
    if len(body) > max_body:
        body = body[:max_body] + "…"
    caption = header + f"<blockquote>{body}</blockquote>"

    _vid = viewer_id if viewer_id else user_id
    nav  = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ PREV", callback_data=f"harem:{page - 1}:{user_id}:{_vid}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("NEXT ➡️", callback_data=f"harem:{page + 1}:{user_id}:{_vid}"))

    kb_rows: list[list] = []
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(f"⛩ CHARACTERS ({total_chars})", callback_data="noop")])
    kb_rows.append([
        InlineKeyboardButton(
            "🔱 Harem Collection",
            switch_inline_query_current_chat=f"harem.{user_id}",
        ),
    ])

    return caption, photo, InlineKeyboardMarkup(kb_rows), total_chars


async def _build_default_body(
    page_chars: list[dict],
    id_counts: dict,
    fav_id,
    all_chars: list,
) -> str:
    anime_groups: dict[str, list[dict]] = {}
    for c in page_chars:
        anime_groups.setdefault(c["anime"], []).append(c)

    anime_totals = {}
    for anime in anime_groups:
        anime_totals[anime] = await waifu_collection.count_documents({"anime": anime})

    lines: list[str] = []
    for anime, achars in anime_groups.items():
        user_cnt = sum(1 for x in all_chars if x["anime"] == anime)
        lines.append(f"⚜️ <b>{escape(anime)}</b> ({user_cnt}/{anime_totals.get(anime, '?')})")
        lines.append("┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄")
        for c in achars:
            rar   = _rarity_icon(c.get("rarity", ""))
            cnt   = id_counts.get(c["id"], 1)
            fav   = " ⭐" if c["id"] == fav_id else ""
            g_rank = (f" 🌐<code>#{c['global_rank']}</code>" if c.get("global_rank") else "")
            lines.append(
                f"🍀 <code>{c['id']}</code> | {rar} | {escape(c['name'])}{fav}{g_rank} (x{cnt})"
            )
        lines.append("")

    return "\n".join(lines).strip()


def _build_detailed_body(
    page_chars: list[dict],
    id_counts: dict,
    fav_id,
) -> str:
    lines: list[str] = []
    for c in page_chars:
        rar   = c.get("rarity", "🎴")
        icon  = _rarity_icon(rar)
        cnt   = id_counts.get(c["id"], 1)
        fav   = " ⭐" if c["id"] == fav_id else ""
        cnt_s = f" ×{cnt}" if cnt > 1 else ""
        lines.append(
            f"🍀 <code>{c['id']}</code>{fav}  <b>{escape(c['name'])}</b>{cnt_s}"
        )
        lines.append(f"    {icon} {escape(rar)}  ·  📺 {escape(c['anime'])}")
        lines.append("")

    return "\n".join(lines).strip()


# ── /harem command ─────────────────────────────────────────────────────────────

async def harem(update: Update, context: CallbackContext, page: int = 0) -> None:
    viewer_id = update.effective_user.id
    target_id = viewer_id

    if context.args:
        arg = context.args[0].strip()
        is_char_id = arg.startswith("0") or len(arg) < 7

        if is_char_id:
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

    mode, sort = await _get_prefs(viewer_id)
    caption, photo, markup, total = await _build_list_view(
        target_id, page, viewer_id=viewer_id, mode=mode, sort=sort
    )

    if total == 0:
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML)
        return

    if photo:
        await update.message.reply_photo(
            photo=photo, caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup,
        )
    else:
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=markup)


# ── Navigation callback ────────────────────────────────────────────────────────

async def harem_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()

    parts     = q.data.split(":")
    page      = int(parts[1])
    uid       = int(parts[2])
    viewer_id = int(parts[3]) if len(parts) >= 4 else uid

    mode, sort = await _get_prefs(viewer_id)
    caption, photo, markup, total = await _build_list_view(
        uid, page, viewer_id=viewer_id, mode=mode, sort=sort
    )

    if total == 0:
        try:
            await q.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML)
        except Exception:
            await q.edit_message_text(caption, parse_mode=ParseMode.HTML)
        return

    if photo:
        try:
            await q.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=markup,
            )
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            try:
                await q.edit_message_caption(
                    caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup,
                )
            except Exception:
                pass
    else:
        try:
            await q.edit_message_text(caption, parse_mode=ParseMode.HTML, reply_markup=markup)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise


# ── /hmode command ─────────────────────────────────────────────────────────────

async def hmode(update: Update, context: CallbackContext) -> None:
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🐉 Default",  callback_data="hmode:set:default"),
            InlineKeyboardButton("Detailed 🦖", callback_data="hmode:set:detailed"),
        ],
        [InlineKeyboardButton("🦕 Reset Preference", callback_data="hmode:reset")],
    ])
    await update.message.reply_text(
        "<b>You Can Change Your Harem Interface Using These Buttons</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def hmode_callback(update: Update, context: CallbackContext) -> None:
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()

    data = q.data  # hmode:set:default | hmode:set:detailed | hmode:reset

    if data == "hmode:reset":
        await user_collection.update_one(
            {"id": uid},
            {"$unset": {"harem_mode": "", "harem_sort": ""}},
            upsert=True,
        )
        await q.edit_message_text(
            "🎨 <b>All Preferences Have Been Reset.</b>🎨",
            parse_mode=ParseMode.HTML,
        )
        return

    # hmode:set:default or hmode:set:detailed
    new_mode = data.split(":")[-1]
    cur_mode, cur_sort = await _get_prefs(uid)

    if cur_mode == new_mode:
        # Already set — show sort options
        mode_label = "🐉 Default" if new_mode == "default" else "🦖 Detailed"
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎖️ Sort By Rarity", callback_data=f"hmode:sort:rarity:{new_mode}"),
                InlineKeyboardButton("📘 Sort By Anime",   callback_data=f"hmode:sort:anime:{new_mode}"),
            ],
            [InlineKeyboardButton("🗑️ Close", callback_data="hmode:close")],
        ])
        await q.edit_message_text(
            f"<b>Your Harem Interface Is Already Set To {mode_label}✅</b>\n\n"
            f"<i>Still You Can Choose How To Sort Your Harem:</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    await user_collection.update_one(
        {"id": uid},
        {"$set": {"harem_mode": new_mode}},
        upsert=True,
    )
    mode_label = "🐉 Default" if new_mode == "default" else "🦖 Detailed"
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎖️ Sort By Rarity", callback_data=f"hmode:sort:rarity:{new_mode}"),
            InlineKeyboardButton("📘 Sort By Anime",   callback_data=f"hmode:sort:anime:{new_mode}"),
        ],
        [InlineKeyboardButton("🗑️ Close", callback_data="hmode:close")],
    ])
    await q.edit_message_text(
        f"✅ <b>Harem Interface changed to {mode_label}!</b>\n\n"
        f"<i>Still You Can Choose How To Sort Your Harem:</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def hmode_sort_callback(update: Update, context: CallbackContext) -> None:
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()

    # hmode:sort:rarity:default
    parts    = q.data.split(":")
    new_sort = parts[2]           # rarity | anime
    cur_mode = parts[3]           # default | detailed

    await user_collection.update_one(
        {"id": uid},
        {"$set": {"harem_sort": new_sort}},
        upsert=True,
    )
    sort_label = "🎖️ By Rarity" if new_sort == "rarity" else "📘 By Anime"
    mode_label = "🐉 Default"   if cur_mode == "default" else "🦖 Detailed"
    await q.edit_message_text(
        f"✅ <b>Sort preference saved!</b>\n\n"
        f"Interface: <b>{mode_label}</b>  |  Sort: <b>{sort_label}</b>\n\n"
        f"<i>Use /harem to see your updated harem.</i>",
        parse_mode=ParseMode.HTML,
    )


async def hmode_close_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()
    try:
        await q.message.delete()
    except Exception:
        pass


# ── send_harem_card (called from other modules) ────────────────────────────────

async def send_harem_card(user_id: int, query) -> None:
    mode, sort = await _get_prefs(user_id)
    caption, photo, markup, total = await _build_list_view(
        user_id, 0, viewer_id=user_id, mode=mode, sort=sort
    )

    if total == 0:
        await query.answer(caption, show_alert=True)
        return

    if photo:
        await query.message.reply_photo(
            photo=photo, caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup,
        )
    else:
        await query.message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=markup)


async def noop(update: Update, context: CallbackContext) -> None:
    await update.callback_query.answer()


# ── Register handlers ──────────────────────────────────────────────────────────

application.add_handler(CommandHandler(["harem", "collection", "waifus", "mywaifu"], harem, block=False))
application.add_handler(CommandHandler("hmode", hmode, block=False))
application.add_handler(CallbackQueryHandler(harem_callback,      pattern=r"^harem:\d+:\d+(:\d+)?$",        block=False))
application.add_handler(CallbackQueryHandler(hmode_callback,      pattern=r"^hmode:set:(default|detailed)$", block=False))
application.add_handler(CallbackQueryHandler(hmode_callback,      pattern=r"^hmode:reset$",                  block=False))
application.add_handler(CallbackQueryHandler(hmode_sort_callback, pattern=r"^hmode:sort:(rarity|anime):",    block=False))
application.add_handler(CallbackQueryHandler(hmode_close_callback,pattern=r"^hmode:close$",                  block=False))
application.add_handler(CallbackQueryHandler(noop,                pattern=r"^noop$",                         block=False))
