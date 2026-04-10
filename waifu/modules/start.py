import random
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, BOT_USERNAME, GROUP_ID, PHOTO_URL, SUPPORT_CHAT, UPDATE_CHAT
from waifu import pm_users as _pm

WELCOME = (
    "👋 <b>Welcome to Waifu Catcher!</b>\n\n"
    "I drop random anime characters in groups.\n"
    "Use <code>/guess</code> to claim them and build your harem!\n\n"
    "📌 Add me to a group to start collecting!"
)

# ── Help sections ──────────────────────────────────────────────────────────────

SECTIONS = {
    "game": (
        "🎮 <b>Game Commands</b>\n\n"
        "/guess [name] — Claim the active character\n"
        "/harem — Your collection (paginated)\n"
        "/fav [id] — Set favourite character\n"
        "/profile — Your stats & level"
    ),
    "economy": (
        "💰 <b>Economy Commands</b>\n\n"
        "/daily — Claim daily coins\n"
        "/balance — Check your coins\n"
        "/market — Browse listings\n"
        "/sell [id] [price] — List a character\n"
        "/buy [listing_id] — Buy from market\n"
        "/delist [listing_id] — Remove your listing"
    ),
    "social": (
        "⚔️ <b>Social Commands</b>\n\n"
        "/trade [char_id] [their_char_id] — Trade (reply to user)\n"
        "/gift [char_id] — Gift a character (reply to user)\n"
        "/duel — Challenge someone to a duel (reply to user)"
    ),
    "leaderboard": (
        "📊 <b>Leaderboard Commands</b>\n\n"
        "/top — Top collectors\n"
        "/ctop — This group's top\n"
        "/TopGroups — Most active groups\n"
        "/stats — Global stats"
    ),
    "settings": (
        "⚙️ <b>Settings & Admin</b>\n\n"
        "/changetime [n] — Drop every n messages (admin)\n"
        "/resettime — Reset to default (admin)\n"
        "/ping — Latency + uptime (sudo)\n"
        "/forcedrop — Trigger instant drop (owner/sudo)"
    ),
    "upload": (
        "📤 <b>Upload Commands</b> (sudo only)\n\n"
        "/upload [file_id or URL] [name] [anime] [rarity]\n"
        "/uploadchar — Reply to image with caption\n"
        "/delete [id] — Remove a character\n"
        "/update [id] [field] [value] — Edit a character\n\n"
        "<b>Rarity numbers:</b>\n"
        "1 → ⚪ Common\n"
        "2 → 🟣 Rare\n"
        "3 → 🟡 Legendary\n"
        "4 → 🔮 Mythical\n"
        "5 → 💮 Special Edition\n"
        "6 → 🌌 Universal Limited\n\n"
        "💡 <b>Tip:</b> Send any photo to me in PM to get its file_id!"
    ),
}

SECTION_LABELS = {
    "game":        "🎮 Game",
    "economy":     "💰 Economy",
    "social":      "⚔️ Social",
    "leaderboard": "📊 Leaderboard",
    "settings":    "⚙️ Settings",
    "upload":      "📤 Upload",
}


def _main_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ Add Me to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=new")],
        [
            InlineKeyboardButton("💬 Support", url=f"https://t.me/{SUPPORT_CHAT}"),
            InlineKeyboardButton("📢 Updates", url=f"https://t.me/{UPDATE_CHAT}"),
        ],
        # Quick actions
        [
            InlineKeyboardButton("📚 My Harem",   callback_data="act:harem"),
            InlineKeyboardButton("👤 Profile",    callback_data="act:profile"),
        ],
        [
            InlineKeyboardButton("🎁 Daily",      callback_data="act:daily"),
            InlineKeyboardButton("💰 Balance",    callback_data="act:balance"),
        ],
        # Help sections
        [
            InlineKeyboardButton("🎮 Game",    callback_data="help:game"),
            InlineKeyboardButton("💰 Economy", callback_data="help:economy"),
        ],
        [
            InlineKeyboardButton("⚔️ Social",      callback_data="help:social"),
            InlineKeyboardButton("📊 Leaderboard", callback_data="help:leaderboard"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="help:settings"),
            InlineKeyboardButton("📤 Upload",   callback_data="help:upload"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def _section_kb(section: str) -> InlineKeyboardMarkup:
    keys = list(SECTIONS.keys())
    idx  = keys.index(section)
    prev_key = keys[idx - 1] if idx > 0 else keys[-1]
    next_key = keys[(idx + 1) % len(keys)]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"◀️ {SECTION_LABELS[prev_key]}", callback_data=f"help:{prev_key}"),
            InlineKeyboardButton(f"{SECTION_LABELS[next_key]} ▶️", callback_data=f"help:{next_key}"),
        ],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="help:home")],
    ])


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    existing = await _pm.find_one({"_id": u.id})
    if existing is None:
        await _pm.insert_one({"_id": u.id, "first_name": u.first_name, "username": u.username})
        try:
            await context.bot.send_message(
                GROUP_ID,
                f"🆕 New user: <a href='tg://user?id={u.id}'>{escape(u.first_name)}</a>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        patch = {}
        if existing.get("first_name") != u.first_name: patch["first_name"] = u.first_name
        if existing.get("username")   != u.username:   patch["username"]   = u.username
        if patch:
            await _pm.update_one({"_id": u.id}, {"$set": patch})

    photo   = random.choice(PHOTO_URL) if PHOTO_URL else None
    caption = WELCOME if update.effective_chat.type == "private" else "🎴 I'm alive! DM me for info."

    if photo:
        await context.bot.send_photo(
            update.effective_chat.id, photo=photo,
            caption=caption, reply_markup=_main_kb(), parse_mode=ParseMode.HTML,
        )
    else:
        await context.bot.send_message(
            update.effective_chat.id,
            text=caption, reply_markup=_main_kb(), parse_mode=ParseMode.HTML,
        )


# ── Callback handler for all help buttons ─────────────────────────────────────

async def button(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()

    data = q.data  # e.g. "help:game" or "help:home"

    if not data.startswith("help:"):
        return

    page = data[5:]  # "game", "economy", ..., "home"

    try:
        if page == "home":
            await q.edit_message_caption(caption=WELCOME, reply_markup=_main_kb(), parse_mode=ParseMode.HTML)
        elif page in SECTIONS:
            await q.edit_message_caption(
                caption=SECTIONS[page],
                reply_markup=_section_kb(page),
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        try:
            if page == "home":
                await q.edit_message_text(WELCOME, reply_markup=_main_kb(), parse_mode=ParseMode.HTML)
            elif page in SECTIONS:
                await q.edit_message_text(
                    SECTIONS[page],
                    reply_markup=_section_kb(page),
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            pass


# ── Quick action callbacks ────────────────────────────────────────────────────

async def action_callback(update: Update, context: CallbackContext) -> None:
    q   = update.callback_query
    uid = q.from_user.id
    act = q.data[4:]  # strip "act:"
    await q.answer()

    if act == "harem":
        from waifu.modules.harem import _build_page
        text, markup, photo = await _build_page(uid, 0)
        try:
            if photo:
                await q.edit_message_caption(caption=text, reply_markup=markup, parse_mode=ParseMode.HTML)
            else:
                await q.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except Exception:
            if photo:
                await q.message.reply_photo(photo, caption=text, reply_markup=markup, parse_mode=ParseMode.HTML)
            else:
                await q.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    elif act == "profile":
        from waifu import user_collection
        import math
        doc = await user_collection.find_one({"id": uid})
        if not doc:
            await q.answer("❌ Profile မတွေ့ဘူး — character တစ်ကောင်တောင် ရဦး!", show_alert=True)
            return
        xp    = doc.get("xp", 0)
        level = 1
        while int(200 * ((level + 1) ** 1.5)) <= xp:
            level += 1
        floor = int(200 * (level ** 1.5))
        nxt   = int(200 * ((level + 1) ** 1.5))
        bar_v = xp - floor
        bar_m = nxt - floor
        filled = int(10 * bar_v / max(bar_m, 1))
        bar    = "▓" * filled + "░" * (10 - filled)
        chars  = doc.get("characters", [])
        text = (
            f"👤 <b>{escape(doc.get('first_name','User'))}</b>\n\n"
            f"⭐ Level {level}  [{bar}]\n"
            f"✨ XP: {xp:,} / {nxt:,}\n\n"
            f"🎴 Characters: {len(chars)}\n"
            f"🏆 Wins: {doc.get('wins', 0)}\n"
            f"💰 Coins: {doc.get('coins', 0):,}"
        )
        try:
            await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML,
                                         reply_markup=InlineKeyboardMarkup([[
                                             InlineKeyboardButton("🏠 Main Menu", callback_data="help:home")
                                         ]]))
        except Exception:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML)

    elif act == "daily":
        import time
        from waifu import user_collection
        from waifu.config import Config as _C
        u   = q.from_user
        doc = await user_collection.find_one({"id": uid}) or {}
        now  = time.time()
        last = doc.get("last_daily", 0)
        cd   = 86400
        if now - last < cd:
            remain = int(cd - (now - last))
            h, r   = divmod(remain, 3600)
            m, s   = divmod(r, 60)
            await q.answer(f"⏳ {h}h {m}m {s}s နောက်မှ ပြန်ယူ", show_alert=True)
            return
        await user_collection.update_one(
            {"id": uid},
            {"$inc": {"coins": _C.DAILY_COINS}, "$set": {"last_daily": now,
             "username": u.username, "first_name": u.first_name}},
            upsert=True,
        )
        await q.answer(f"🎁 {_C.DAILY_COINS} coins ရပြီ!", show_alert=True)

    elif act == "balance":
        from waifu import user_collection
        doc = await user_collection.find_one({"id": uid}) or {}
        await q.answer(
            f"💰 {escape(q.from_user.first_name)}: {doc.get('coins', 0):,} coins",
            show_alert=True,
        )


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler("start", start, block=False))
application.add_handler(CallbackQueryHandler(button,          pattern=r"^help:", block=False))
application.add_handler(CallbackQueryHandler(action_callback, pattern=r"^act:",  block=False))
