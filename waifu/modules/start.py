from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from waifu import (
    application, BOT_USERNAME, GROUP_ID, PHOTO_URL,
    SUPPORT_CHAT, UPDATE_CHAT, OWNER_ID,
    bot_settings_collection,
)
from waifu import pm_users as _pm

WELCOME = (
    "👋\n\n"
    "<blockquote>I drop random anime characters in groups.\n"
    "Use /guess to claim them and build your harem!</blockquote>"
)

# ── Welcome photo rotation ────────────────────────────────────────────────────

_photo_idx: int = 0   # global rotation cursor


async def _get_welcome_photos() -> list[str]:
    """Return list of photo file_ids from DB, fallback to env PHOTO_URL."""
    doc = await bot_settings_collection.find_one({"_id": "welcome_photos"})
    if doc and doc.get("photos"):
        return doc["photos"]
    return list(PHOTO_URL)   # fallback


async def _next_photo() -> str | None:
    """Return next photo in rotation (cyclic), or None if list is empty."""
    global _photo_idx
    photos = await _get_welcome_photos()
    if not photos:
        return None
    photo = photos[_photo_idx % len(photos)]
    _photo_idx = (_photo_idx + 1) % len(photos)
    return photo

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
        "/changetime [n] — n မိနစ်တိုင်း drop (admin)\n"
        "/resettime — Default ပြန်သတ်မှတ် (admin)\n"
        "/ping — Latency + uptime (sudo)\n"
        "/forcedrop — Instant drop (owner/sudo)"
    ),
}

SECTION_LABELS = {
    "game":        "🎮 Game",
    "economy":     "💰 Economy",
    "social":      "⚔️ Social",
    "leaderboard": "📊 Leaderboard",
    "settings":    "⚙️ Settings",
}


def _group_kb(uid: int | None = None) -> InlineKeyboardMarkup:
    """3-button keyboard shown when /start is used inside a group."""
    waifus_btn = (
        InlineKeyboardButton("🔱 My Waifus", switch_inline_query_current_chat=f"harem.{uid}")
        if uid else
        InlineKeyboardButton("🔱 My Waifus", callback_data="act:harem")
    )
    return InlineKeyboardMarkup([
        [waifus_btn,
         InlineKeyboardButton("👤 Profile", callback_data="act:profile")],
        [InlineKeyboardButton("👨‍💻 Dᴇᴠᴇʟᴏᴘᴇʀ", url=f"tg://user?id={OWNER_ID}")],
    ])


def _main_kb(uid: int | None = None) -> InlineKeyboardMarkup:
    harem_btn = (
        InlineKeyboardButton("🔱 My Waifus", switch_inline_query_current_chat=f"harem.{uid}")
        if uid else
        InlineKeyboardButton("🔱 My Waifus", callback_data="act:harem")
    )
    rows = [
        [InlineKeyboardButton("➕ Add Me to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=new")],
        [
            InlineKeyboardButton("💬 Support", url="https://t.me/manhwlunachat"),
            InlineKeyboardButton("📢 Updates", url="https://t.me/+py0voloZgOQ5NGU1"),
        ],
        # Quick actions
        [
            harem_btn,
            InlineKeyboardButton("👤 Profile",  callback_data="act:profile"),
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
    ]
    return InlineKeyboardMarkup(rows)


def _owner_kb(uid: int | None = None) -> InlineKeyboardMarkup:
    """Extra panel shown ONLY to the owner in their PM."""
    harem_btn = (
        InlineKeyboardButton("🔱 My Waifus", switch_inline_query_current_chat=f"harem.{uid}")
        if uid else
        InlineKeyboardButton("🔱 My Waifus", callback_data="act:harem")
    )
    rows = [
        [InlineKeyboardButton("➕ Add Me to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=new")],
        [
            InlineKeyboardButton("💬 Support", url="https://t.me/manhwlunachat"),
            InlineKeyboardButton("📢 Updates", url="https://t.me/+py0voloZgOQ5NGU1"),
        ],
        # Quick actions
        [
            harem_btn,
            InlineKeyboardButton("👤 Profile",  callback_data="act:profile"),
        ],
        [
            InlineKeyboardButton("🎁 Daily",    callback_data="act:daily"),
            InlineKeyboardButton("💰 Balance",  callback_data="act:balance"),
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
        ],
        # ── Owner Panel ──────────────────────────────────
        [InlineKeyboardButton("👑 ─── Owner Panel ─── 👑", callback_data="owner:noop")],
        [
            InlineKeyboardButton("📤 Upload Char",    callback_data="owner:upload"),
            InlineKeyboardButton("⚡ Force Drop",     callback_data="owner:forcedrop"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast",      callback_data="owner:broadcast"),
            InlineKeyboardButton("🗑 Delete Char",    callback_data="owner:delete"),
        ],
        [
            InlineKeyboardButton("🔧 Update Char",    callback_data="owner:update"),
            InlineKeyboardButton("👤 Sudo Users",     callback_data="owner:sudo"),
        ],
        [
            InlineKeyboardButton("🖼 Welcome Photos", callback_data="owner:welcomephotos"),
            InlineKeyboardButton("📊 Bot Stats",      callback_data="owner:stats"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


OWNER_WELCOME = (
    "👑 <b>Welcome back, Owner!</b>\n\n"
    "I drop random anime characters in groups.\n"
    "Use <code>/guess</code> to claim them!\n\n"
    "🛡 <b>Owner Panel</b> — Bot ကို ထိန်းချုပ်ဖို့\n"
    "အောက်က buttons တွေ သုံးနိုင်တယ်:"
)


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

    photo      = await _next_photo()
    is_pm      = update.effective_chat.type == "private"
    is_owner   = u.id == OWNER_ID

    if is_pm and is_owner:
        caption = OWNER_WELCOME
        markup  = _owner_kb(u.id)
    elif is_pm:
        caption = WELCOME
        markup  = _main_kb(u.id)
    else:
        caption = (
            "🌸 <b>ᴡᴀɪꜰᴜ ᴄᴀᴛᴄʜᴇʀ</b> 🌸\n\n"
            "<blockquote>ᴄʜᴀʀᴀᴄᴛᴇʀꜱ ᴅʀᴏᴘ ʜᴇʀᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ.\n"
            "ᴜꜱᴇ /ɢᴜᴇꜱꜱ ᴛᴏ ᴄʟᴀɪᴍ ᴛʜᴇᴍ!</blockquote>"
        )
        markup  = _group_kb(u.id)

    if photo:
        await context.bot.send_photo(
            update.effective_chat.id, photo=photo,
            caption=caption, reply_markup=markup, parse_mode=ParseMode.HTML,
        )
    else:
        await context.bot.send_message(
            update.effective_chat.id,
            text=caption, reply_markup=markup, parse_mode=ParseMode.HTML,
        )


# ── Callback handler for all help buttons ─────────────────────────────────────

async def button(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()

    data = q.data  # e.g. "help:game" or "help:home"

    if not data.startswith("help:"):
        return

    page    = data[5:]  # "game", "economy", ..., "home"
    uid     = q.from_user.id
    is_group = q.message.chat.type in ("group", "supergroup")

    # In groups: "home" always shows the slim 4-button group keyboard
    if page == "home" and is_group:
        grp_caption = (
            "🌸 <b>ᴡᴀɪꜰᴜ ᴄᴀᴛᴄʜᴇʀ</b> 🌸\n\n"
            "<blockquote>ᴄʜᴀʀᴀᴄᴛᴇʀꜱ ᴅʀᴏᴘ ʜᴇʀᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ.\n"
            "ᴜꜱᴇ /ɢᴜᴇꜱꜱ ᴛᴏ ᴄʟᴀɪᴍ ᴛʜᴇᴍ!</blockquote>"
        )
        try:
            await q.edit_message_caption(caption=grp_caption, reply_markup=_group_kb(uid), parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await q.edit_message_text(grp_caption, reply_markup=_group_kb(uid), parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    try:
        if page == "home":
            await q.edit_message_caption(caption=WELCOME, reply_markup=_main_kb(uid), parse_mode=ParseMode.HTML)
        elif page in SECTIONS:
            await q.edit_message_caption(
                caption=SECTIONS[page],
                reply_markup=_section_kb(page),
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        try:
            if page == "home":
                await q.edit_message_text(WELCOME, reply_markup=_main_kb(uid), parse_mode=ParseMode.HTML)
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

    # daily & balance answer themselves with show_alert — don't pre-answer
    if act not in ("daily", "balance"):
        await q.answer()

    if act == "harem":
        from waifu.modules.harem import send_harem_card
        await send_harem_card(uid, q)
        return

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


# ── Owner panel callbacks ─────────────────────────────────────────────────────

OWNER_CMD_INFO = {
    "upload":    ("📤 <b>Upload Character</b>\n\nPM ထဲမှာ ပုံ တိုက်ရိုက်ပို့ (သို့) /upload ရိုက်ပေး",),
    "forcedrop": ("⚡ <b>Force Drop</b>\n\nGroup ထဲမှာ <code>/forcedrop</code> ရိုက်ပေး\n\n"
                  "Drop interval ပြောင်းရန်:\n<code>/changetime [မိနစ်]</code>",),
    "broadcast": ("📢 <b>Broadcast</b>\n\n<code>/broadcast [message]</code> ရိုက်ပေး",),
    "delete":    ("🗑 <b>Delete Character</b>\n\n<code>/delete [ID]</code> ရိုက်ပေး",),
    "update":    ("🔧 <b>Update Character</b>\n\n<code>/update [ID] [field] [value]</code>\nFields: name, anime, rarity, img_url",),
    "sudo":      ("👤 <b>Sudo Users</b>\n\n<code>/addsudo [user_id]</code>\n<code>/removesudo [user_id]</code>",),
    "stats":     ("📊 <b>Bot Stats</b>\n\n<code>/stats</code> ရိုက်ပေး",),
    "welcomephotos": (
        "🖼 <b>Welcome Photos</b>\n\n"
        "ပုံ ထည့်ရန်:\nBot PM ထဲမှာ ပုံ ပို့ပြီး <code>/addwelcomephoto</code> reply လုပ်ပေး\n\n"
        "ပုံ စာရင်းကြည့်ရန်:\n<code>/welcomephotos</code>\n\n"
        "ပုံ ဖျက်ရန်:\n<code>/removewelcomephoto &lt;number&gt;</code>",
    ),
}


async def owner_callback(update: Update, context: CallbackContext) -> None:
    q   = update.callback_query
    uid = q.from_user.id

    if uid != OWNER_ID:
        await q.answer("❌ Owner only!", show_alert=True)
        return

    action = q.data[6:]  # strip "owner:"

    if action == "noop":
        await q.answer("👑 Owner Panel", show_alert=False)
        return

    if action == "home":
        await q.answer()
        try:
            await q.edit_message_caption(
                caption=OWNER_WELCOME, reply_markup=_owner_kb(), parse_mode=ParseMode.HTML)
        except Exception:
            await q.edit_message_text(
                OWNER_WELCOME, reply_markup=_owner_kb(), parse_mode=ParseMode.HTML)
        return

    info = OWNER_CMD_INFO.get(action)
    if info:
        await q.answer()
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Owner Menu", callback_data="owner:home")]
        ])
        try:
            await q.edit_message_caption(
                caption=info[0], reply_markup=back_kb, parse_mode=ParseMode.HTML)
        except Exception:
            await q.edit_message_text(
                info[0], reply_markup=back_kb, parse_mode=ParseMode.HTML)
    else:
        await q.answer("⚠️ Unknown action", show_alert=True)


# ── Welcome photo management commands (owner only) ────────────────────────────

async def addwelcomephoto(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    msg = update.message

    # Support replying to a photo OR sending a photo with the command as caption
    target = msg.reply_to_message if msg.reply_to_message else msg
    photo  = (target.photo[-1].file_id if target.photo
              else (target.document.file_id if target.document and
                    target.document.mime_type and
                    target.document.mime_type.startswith("image") else None))

    if not photo:
        await msg.reply_text(
            "❌ ပုံ bot PM ထဲ ပို့ပြီး ထိုပုံကို reply လုပ်ကာ /addwelcomephoto ရိုက်ပေး"
        )
        return

    doc = await bot_settings_collection.find_one({"_id": "welcome_photos"}) or {}
    photos = doc.get("photos", [])
    if photo in photos:
        await msg.reply_text("⚠️ ဒီပုံ ရှိပြီးသားပဲ")
        return

    photos.append(photo)
    await bot_settings_collection.update_one(
        {"_id": "welcome_photos"},
        {"$set": {"photos": photos}},
        upsert=True,
    )
    await msg.reply_text(
        f"✅ Welcome ပုံ ထည့်ပြီ!\n🖼 စုစုပေါင်း: <b>{len(photos)}</b> ပုံ",
        parse_mode=ParseMode.HTML,
    )


async def listwelcomephotos(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    photos = await _get_welcome_photos()
    if not photos:
        await update.message.reply_text("📭 Welcome ပုံ မရှိသေးဘူး")
        return
    lines = [f"{i+1}. <code>{p[:30]}…</code>" for i, p in enumerate(photos)]
    await update.message.reply_text(
        f"🖼 <b>Welcome Photos ({len(photos)} ပုံ)</b>\n\n" + "\n".join(lines) +
        "\n\nဖျက်ရန်: /removewelcomephoto &lt;number&gt;",
        parse_mode=ParseMode.HTML,
    )


async def removewelcomephoto(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /removewelcomephoto <number>")
        return

    n = int(context.args[0]) - 1
    doc = await bot_settings_collection.find_one({"_id": "welcome_photos"}) or {}
    photos = doc.get("photos", [])

    if n < 0 or n >= len(photos):
        await update.message.reply_text(f"❌ {n+1} မဟုတ်တဲ့ နံပါတ် — 1–{len(photos)} ထဲကရွေး")
        return

    removed = photos.pop(n)
    await bot_settings_collection.update_one(
        {"_id": "welcome_photos"},
        {"$set": {"photos": photos}},
        upsert=True,
    )
    await update.message.reply_text(
        f"🗑 ပုံ {n+1} ဖျက်ပြီ!\n🖼 ကျန်: <b>{len(photos)}</b> ပုံ",
        parse_mode=ParseMode.HTML,
    )


# ── /help — Help Center ───────────────────────────────────────────────────────

_HC_SECTIONS: dict[str, dict] = {
    "catch": {
        "text": (
            "🎮 <b>Character ဖမ်းနည်း</b>\n\n"
            "<code>/guess [name]</code> — Claim the active character\n"
            "<code>/harem</code>       — Your collection (paginated)\n"
            "<code>/fav [id]</code>    — Set favourite character\n"
            "<code>/profile</code>     — Your stats & level"
        ),
        "nav": [("💰 Eco", "economy"), ("⚔️ Social", "social")],
    },
    "economy": {
        "text": (
            "💰 <b>Economy Commands</b>\n\n"
            "<code>/daily</code>              — Claim daily coins\n"
            "<code>/balance</code>            — Check your coins\n"
            "<code>/market</code>             — Browse listings\n"
            "<code>/sell [id] [price]</code>  — List a character\n"
            "<code>/buy [listing_id]</code>   — Buy from market\n"
            "<code>/delist [listing_id]</code>— Remove your listing"
        ),
        "nav": [("🎮 Catch", "catch"), ("⚔️ Social", "social")],
    },
    "social": {
        "text": (
            "⚔️ <b>Social Commands</b>\n\n"
            "<code>/trade [char_id] [their_char_id]</code> — Trade (reply to user)\n"
            "<code>/gift [char_id]</code>                  — Gift a character (reply to user)\n"
            "<code>/duel</code>                            — Challenge to a duel (reply to user)"
        ),
        "nav": [("💰 Eco", "economy"), ("📊 LBoard", "leaderboard")],
    },
    "leaderboard": {
        "text": (
            "📊 <b>Leaderboard Commands</b>\n\n"
            "<code>/top</code>       — Top collectors\n"
            "<code>/ctop</code>      — This group's top\n"
            "<code>/TopGroups</code> — Most active groups\n"
            "<code>/stats</code>     — Global stats"
        ),
        "nav": [("⚔️ Social", "social"), ("🎮 Catch", "catch")],
    },
}


def _hc_kb(section: str) -> InlineKeyboardMarkup:
    nav = _HC_SECTIONS[section]["nav"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"hc:{key}") for label, key in nav],
        [InlineKeyboardButton("📥 Get Out 📥", callback_data="hc:close")],
    ])


async def help_cmd(update: Update, context: CallbackContext) -> None:
    photo   = await _next_photo()
    bot_me  = await context.bot.get_me()
    bot_tag = f'<a href="tg://user?id={bot_me.id}">@{bot_me.username}</a>'
    caption = (
        f"ʜɪ ɪ ᴀᴍ {bot_tag}\n"
        f"<blockquote>Waifu help Center Below👇🏻</blockquote>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👮🏻‍♂️ Help Center 👮🏻‍♂️", callback_data="hc:catch")]
    ])
    if photo:
        await update.message.reply_photo(
            photo=photo, caption=caption,
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
    else:
        await update.message.reply_text(
            caption, parse_mode=ParseMode.HTML, reply_markup=kb,
        )


async def helpcenter_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()
    page = q.data[3:]   # strip "hc:"

    if page == "close":
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    sec = _HC_SECTIONS.get(page)
    if not sec:
        return

    try:
        await q.edit_message_caption(
            caption=sec["text"],
            parse_mode=ParseMode.HTML,
            reply_markup=_hc_kb(page),
        )
    except Exception:
        try:
            await q.edit_message_text(
                sec["text"],
                parse_mode=ParseMode.HTML,
                reply_markup=_hc_kb(page),
            )
        except Exception:
            pass


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler("start",                start,               block=False))
application.add_handler(CommandHandler("help",                 help_cmd,            block=False))
application.add_handler(CommandHandler("addwelcomephoto",      addwelcomephoto,     block=False))
application.add_handler(CommandHandler("welcomephotos",        listwelcomephotos,   block=False))
application.add_handler(CommandHandler("removewelcomephoto",   removewelcomephoto,  block=False))
application.add_handler(CallbackQueryHandler(helpcenter_callback, pattern=r"^hc:",   block=False))
application.add_handler(CallbackQueryHandler(button,          pattern=r"^help:",   block=False))
application.add_handler(CallbackQueryHandler(action_callback, pattern=r"^act:",    block=False))
application.add_handler(CallbackQueryHandler(owner_callback,  pattern=r"^owner:",  block=False))
