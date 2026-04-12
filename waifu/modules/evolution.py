"""
modules/evolution.py — Evolution / Star Upgrade system.

Regular cards (duplicate-based):
  2 copies  → 1★  (consumes 1 extra)
  5 copies  → 2★  (consumes 4 extra)
  10 copies → 3★  (consumes 9 extra)

Global rarity (coin-based):
  1★ = 1 500 🪙  → +3 000 Wanted Coins
  2★ = 2 000 🪙  → +4 000 Wanted Coins
  3★ = 2 500 🪙  → +6 000 Wanted Coins  ← broadcast to all groups (no char name)

Special Edition (coin-based):
  1★ = 2 000 🪙  → +5 000 Wanted Coins
  2★ = 4 000 🪙  → +10 000 Wanted Coins
  3★ = 6 000 🪙  → +20 000 Wanted Coins  ← broadcast to all groups (no char name)

Universal Limited (Black Material-based):
  1★ = 2 🔩 BM   → +20 000 Wanted Coins
  2★ = 4 🔩 BM   → +40 000 Wanted Coins
  3★ = 6 🔩 BM   → +100 000 Wanted Coins ← God Of Waifu badge + broadcast all groups

Stars stored in user_collection as  waifu_stars: {char_id: N}
"""
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, registered_chats, LOGGER

# ── Constants ─────────────────────────────────────────────────────────────────

STAR_THRESHOLDS: dict[int, int] = {1: 2, 2: 5, 3: 10}   # copies needed (regular)

GLOBAL_RARITY   = "🌐 Global"
SPECIAL_RARITY  = "💮 Special Edition"
UL_RARITY       = "🌌 Universal Limited"

# Coin costs
GLOBAL_COIN_COSTS: dict[int, int]  = {1: 1_500, 2: 2_000, 3: 2_500}
SPECIAL_COIN_COSTS: dict[int, int] = {1: 2_000, 2: 4_000, 3: 6_000}

# Black Material costs for Universal Limited
UL_BM_COSTS: dict[int, int] = {1: 2, 2: 4, 3: 6}

# Wanted Coins rewarded on upgrade (per star level)
GLOBAL_WC:  dict[int, int] = {1: 3_000, 2: 4_000,  3: 6_000}
SPECIAL_WC: dict[int, int] = {1: 5_000, 2: 10_000, 3: 20_000}
UL_WC:      dict[int, int] = {1: 20_000, 2: 40_000, 3: 100_000}

# Coin-based rarities (not duplicate-based)
COIN_RARITIES = {GLOBAL_RARITY, SPECIAL_RARITY}

# God of Waifu badge (awarded on UL 3★)
GOD_BADGE = "🌌 God Of Waifu"

_PAGE = 6   # waifus shown per page


# ── Helpers ───────────────────────────────────────────────────────────────────

def stars_display(n: int) -> str:
    return "★" * n + "☆" * (3 - n)


def _can_evolve(count: int, current_stars: int, rarity: str) -> list[int]:
    levels = []
    if rarity in (GLOBAL_RARITY, SPECIAL_RARITY, UL_RARITY):
        for s in (1, 2, 3):
            if s > current_stars:
                levels.append(s)
    else:
        for s in (1, 2, 3):
            if s > current_stars and count >= STAR_THRESHOLDS[s]:
                levels.append(s)
    return levels


def _cost_line(rarity: str, star: int, count: int, bm: int = 0) -> str:
    if rarity == GLOBAL_RARITY:
        wc = GLOBAL_WC[star]
        return f"{GLOBAL_COIN_COSTS[star]:,} 🪙  → +{wc:,} Wanted Coins"
    if rarity == SPECIAL_RARITY:
        wc = SPECIAL_WC[star]
        return f"{SPECIAL_COIN_COSTS[star]:,} 🪙  → +{wc:,} Wanted Coins"
    if rarity == UL_RARITY:
        need_bm = UL_BM_COSTS[star]
        wc = UL_WC[star]
        have = f" (have {bm})" if bm < need_bm else ""
        return f"{need_bm} 🔩 Black Material{have}  → +{wc:,} Wanted Coins"
    needed = STAR_THRESHOLDS[star]
    extra  = needed - 1
    return f"{extra} duplicate card{'s' if extra > 1 else ''} (need {needed} total, have {count})"


# ── Build evolvable list ───────────────────────────────────────────────────────

async def _get_evolvable(user_id: int) -> list[dict]:
    user = await user_collection.find_one({"id": user_id})
    if not user:
        return []

    chars      = user.get("characters", [])
    stars_map  = user.get("waifu_stars", {})

    id_counts: dict[str, int] = {}
    id_to_char: dict[str, dict] = {}
    for c in chars:
        cid = c["id"]
        id_counts[cid] = id_counts.get(cid, 0) + 1
        id_to_char[cid] = c

    result = []
    for cid, char in id_to_char.items():
        count  = id_counts[cid]
        stars  = stars_map.get(cid, 0)
        rarity = char.get("rarity", "")
        if _can_evolve(count, stars, rarity):
            result.append({"cid": cid, "char": char, "count": count, "stars": stars})

    result.sort(key=lambda x: x["char"].get("rarity", ""))
    return result


# ── /update command ───────────────────────────────────────────────────────────

async def update_cmd(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    await _show_list(update.message.reply_text, user_id, page=0)


async def _show_list(reply_fn, user_id: int, page: int) -> None:
    items = await _get_evolvable(user_id)

    if not items:
        await reply_fn(
            "🌟 <b>Evolution / Ascension</b>\n\n"
            "❌ ယခုမ upgrade လုပ်နိုင်တဲ့ waifu မရှိသေးဘူး!\n\n"
            "<i>Duplicate waifu ပိုရ (သို့) Premium card ရမှ ဒီ menu မှာ ပေါ်လာမယ်</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    total_pages = max(1, (len(items) + _PAGE - 1) // _PAGE)
    page        = max(0, min(page, total_pages - 1))
    slice_      = items[page * _PAGE: (page + 1) * _PAGE]

    lines = [f"🌟 <b>Evolution / Ascension</b>  [{page+1}/{total_pages}]\n"]
    for it in slice_:
        c      = it["char"]
        rarity = c.get("rarity", "?")
        s_disp = stars_display(it["stars"])
        if rarity == UL_RARITY:
            badge = "🌌"
        elif rarity == GLOBAL_RARITY:
            badge = "🌐"
        elif rarity == SPECIAL_RARITY:
            badge = "💮"
        elif "Mythical" in rarity:
            badge = "🔮"
        elif "Legendary" in rarity:
            badge = "🟡"
        elif "Medium" in rarity:
            badge = "🟤"
        elif "Rare" in rarity:
            badge = "🟣"
        else:
            badge = "⚪"
        lines.append(
            f"{badge} <b>{escape(c['name'])}</b>  {s_disp}\n"
            f"   ×{it['count']} copies  •  <i>{rarity}</i>"
        )

    text = "\n\n".join(lines)

    btns = []
    for it in slice_:
        c = it["char"]
        btns.append([InlineKeyboardButton(
            f"{stars_display(it['stars'])} {c['name'][:22]}",
            callback_data=f"evo:view:{it['cid']}:{page}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"evo:list:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"evo:list:{page+1}"))
    if nav:
        btns.append(nav)

    await reply_fn(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)


# ── View single waifu evolution options ───────────────────────────────────────

async def _show_view(query, user_id: int, cid: str, back_page: int) -> None:
    user = await user_collection.find_one({"id": user_id})
    if not user:
        await query.answer("User not found.", show_alert=True)
        return

    chars     = user.get("characters", [])
    stars_map = user.get("waifu_stars", {})
    coins     = user.get("coins", 0)
    bm        = user.get("black_material", 0)

    count = sum(1 for c in chars if c["id"] == cid)
    char  = next((c for c in chars if c["id"] == cid), None)
    if not char:
        await query.answer("Character not found.", show_alert=True)
        return

    stars  = stars_map.get(cid, 0)
    rarity = char.get("rarity", "")
    levels = _can_evolve(count, stars, rarity)

    bm_line = f"\n🔩 Black Material: {bm}" if rarity == UL_RARITY else ""

    text = (
        f"⚗️ <b>Evolution — {escape(char['name'])}</b>\n\n"
        f"📊 Rarity: {rarity}\n"
        f"🃏 Copies: ×{count}\n"
        f"⭐ Stars: {stars_display(stars)} ({stars}/3)\n"
        f"💰 Coins: {coins:,} 🪙{bm_line}\n\n"
    )

    if not levels:
        text += "✅ Max star (3★) ရောက်ပြီ!"
        btns = [[InlineKeyboardButton("🔙 Back", callback_data=f"evo:list:{back_page}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)
        return

    text += "<b>Upgrade options:</b>\n"
    for s in levels:
        cost = _cost_line(rarity, s, count, bm)
        text += f"\n  {stars_display(s)} → {cost}"

    btns = []
    for s in levels:
        if rarity == GLOBAL_RARITY:
            needed_coins = GLOBAL_COIN_COSTS[s]
            wc           = GLOBAL_WC[s]
            if coins < needed_coins:
                btns.append([InlineKeyboardButton(
                    f"❌ {stars_display(s)} — {needed_coins:,} 🪙 (coin မလုံ)",
                    callback_data="evo:noop",
                )])
            else:
                btns.append([InlineKeyboardButton(
                    f"⬆️ {stars_display(s)} — {needed_coins:,} 🪙 (+{wc:,} WC)",
                    callback_data=f"evo:do:{cid}:{s}:{back_page}",
                )])

        elif rarity == SPECIAL_RARITY:
            needed_coins = SPECIAL_COIN_COSTS[s]
            wc           = SPECIAL_WC[s]
            if coins < needed_coins:
                btns.append([InlineKeyboardButton(
                    f"❌ {stars_display(s)} — {needed_coins:,} 🪙 (coin မလုံ)",
                    callback_data="evo:noop",
                )])
            else:
                btns.append([InlineKeyboardButton(
                    f"⬆️ {stars_display(s)} — {needed_coins:,} 🪙 (+{wc:,} WC)",
                    callback_data=f"evo:do:{cid}:{s}:{back_page}",
                )])

        elif rarity == UL_RARITY:
            need_bm = UL_BM_COSTS[s]
            wc      = UL_WC[s]
            if bm < need_bm:
                btns.append([InlineKeyboardButton(
                    f"❌ {stars_display(s)} — {need_bm} 🔩 BM (BM မလုံ: {bm}/{need_bm})",
                    callback_data="evo:noop",
                )])
            else:
                btns.append([InlineKeyboardButton(
                    f"⬆️ {stars_display(s)} — {need_bm} 🔩 BM (+{wc:,} WC)",
                    callback_data=f"evo:do:{cid}:{s}:{back_page}",
                )])

        else:
            needed = STAR_THRESHOLDS[s]
            if count < needed:
                btns.append([InlineKeyboardButton(
                    f"❌ {stars_display(s)} (need {needed}, have {count})",
                    callback_data="evo:noop",
                )])
            else:
                extra = needed - 1
                btns.append([InlineKeyboardButton(
                    f"⬆️ Evolve to {stars_display(s)} (use {extra} card{'s' if extra>1 else ''})",
                    callback_data=f"evo:do:{cid}:{s}:{back_page}",
                )])

    btns.append([InlineKeyboardButton("🔙 Back", callback_data=f"evo:list:{back_page}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)


# ── Execute evolution ──────────────────────────────────────────────────────────

async def _do_evolve(query, user_id: int, cid: str, target_stars: int, back_page: int) -> None:
    user = await user_collection.find_one({"id": user_id})
    if not user:
        await query.answer("User not found.", show_alert=True)
        return

    chars     = user.get("characters", [])
    stars_map = user.get("waifu_stars", {})
    coins     = user.get("coins", 0)
    bm        = user.get("black_material", 0)

    count   = sum(1 for c in chars if c["id"] == cid)
    char    = next((c for c in chars if c["id"] == cid), None)
    if not char:
        await query.answer("Character not found.", show_alert=True)
        return

    current_stars = stars_map.get(cid, 0)
    rarity        = char.get("rarity", "")

    if target_stars <= current_stars:
        await query.answer("Already at this star level!", show_alert=True)
        return

    # ── Global: deduct coins + give Wanted Coins ──────────────────────────────
    if rarity == GLOBAL_RARITY:
        cost    = GLOBAL_COIN_COSTS[target_stars]
        wc_gain = GLOBAL_WC[target_stars]
        if coins < cost:
            await query.answer(f"Coins မလုံ! ({coins:,}/{cost:,} 🪙)", show_alert=True)
            return
        await user_collection.update_one(
            {"id": user_id},
            {
                "$inc": {"coins": -cost, "wanted_coins": wc_gain},
                "$set": {f"waifu_stars.{cid}": target_stars},
            },
        )
        await query.answer(
            f"✨ {char['name']} → {stars_display(target_stars)}  "
            f"(-{cost:,} 🪙  +{wc_gain:,} WC)",
            show_alert=True,
        )

    # ── Special Edition: deduct coins + give Wanted Coins ─────────────────────
    elif rarity == SPECIAL_RARITY:
        cost    = SPECIAL_COIN_COSTS[target_stars]
        wc_gain = SPECIAL_WC[target_stars]
        if coins < cost:
            await query.answer(f"Coins မလုံ! ({coins:,}/{cost:,} 🪙)", show_alert=True)
            return
        await user_collection.update_one(
            {"id": user_id},
            {
                "$inc": {"coins": -cost, "wanted_coins": wc_gain},
                "$set": {f"waifu_stars.{cid}": target_stars},
            },
        )
        await query.answer(
            f"✨ {char['name']} → {stars_display(target_stars)}  "
            f"(-{cost:,} 🪙  +{wc_gain:,} WC)",
            show_alert=True,
        )

    # ── Universal Limited: deduct Black Material + give Wanted Coins ──────────
    elif rarity == UL_RARITY:
        need_bm = UL_BM_COSTS[target_stars]
        wc_gain = UL_WC[target_stars]
        if bm < need_bm:
            await query.answer(f"Black Material မလုံ! ({bm}/{need_bm} 🔩)", show_alert=True)
            return
        await user_collection.update_one(
            {"id": user_id},
            {
                "$inc": {"black_material": -need_bm, "wanted_coins": wc_gain},
                "$set": {f"waifu_stars.{cid}": target_stars},
            },
        )
        await query.answer(
            f"✨ {char['name']} → {stars_display(target_stars)}  "
            f"(-{need_bm} 🔩  +{wc_gain:,} WC)",
            show_alert=True,
        )

    # ── Regular waifu: remove duplicate copies ────────────────────────────────
    else:
        needed = STAR_THRESHOLDS[target_stars]
        if count < needed:
            await query.answer(f"Copies မလုံ! ({count}/{needed})", show_alert=True)
            return

        to_remove = needed - 1
        new_chars = list(chars)
        final = []
        kept_one = False
        removed  = 0
        for c in new_chars:
            if c["id"] == cid:
                if not kept_one:
                    final.append(c)
                    kept_one = True
                elif removed < to_remove:
                    removed += 1
                else:
                    final.append(c)
            else:
                final.append(c)

        await user_collection.update_one(
            {"id": user_id},
            {
                "$set": {
                    "characters": final,
                    f"waifu_stars.{cid}": target_stars,
                },
            },
        )
        await query.answer(
            f"✨ {char['name']} → {stars_display(target_stars)}  "
            f"(-{to_remove} card{'s' if to_remove>1 else ''})",
            show_alert=True,
        )

    # ── 3★ announcements for premium rarities ─────────────────────────────────
    if target_stars == 3 and rarity in (GLOBAL_RARITY, SPECIAL_RARITY, UL_RARITY):
        user_doc  = await user_collection.find_one({"id": user_id})
        fn        = escape((user_doc or {}).get("first_name", "Someone"))
        mention   = f'<a href="tg://user?id={user_id}">{fn}</a>'

        if rarity == UL_RARITY:
            # Give God Of Waifu badge
            await user_collection.update_one(
                {"id": user_id},
                {"$addToSet": {"badges": GOD_BADGE}},
            )
            ann_text = (
                f"🌌 <b>GOD OF WAIFU!</b>\n\n"
                f"{mention} has reached <b>3★</b> on a "
                f"<b>{rarity}</b> character!\n"
                f"🏅 Badge awarded: <b>{GOD_BADGE}</b>"
            )
        else:
            ann_text = (
                f"✨ <b>3★ Achieved!</b>\n\n"
                f"{mention} has reached <b>3★</b> on a "
                f"<b>{rarity}</b> character!"
            )

        for gid in list(registered_chats):
            try:
                await query.bot.send_message(gid, ann_text, parse_mode=ParseMode.HTML)
            except Exception as e:
                LOGGER.debug("3★ announce to %s failed: %s", gid, e)

    # Refresh the view
    await _show_view(query, user_id, cid, back_page)


# ── Callback dispatcher ───────────────────────────────────────────────────────

async def _evo_callback(update: Update, context: CallbackContext) -> None:
    q       = update.callback_query
    user_id = q.from_user.id
    data    = q.data

    await q.answer()

    parts = data.split(":")

    if parts[1] == "noop":
        return

    elif parts[1] == "list":
        page = int(parts[2])
        await _show_list(q.edit_message_text, user_id, page=page)

    elif parts[1] == "view":
        cid       = parts[2]
        back_page = int(parts[3])
        await _show_view(q, user_id, cid, back_page)

    elif parts[1] == "do":
        cid          = parts[2]
        target_stars = int(parts[3])
        back_page    = int(parts[4])
        await _do_evolve(q, user_id, cid, target_stars, back_page)


# ── Register ──────────────────────────────────────────────────────────────────

application.add_handler(CommandHandler("update", update_cmd, block=False))
application.add_handler(CallbackQueryHandler(
    _evo_callback, pattern=r"^evo:", block=False
))
