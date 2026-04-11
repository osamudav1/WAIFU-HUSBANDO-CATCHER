"""
modules/evolution.py — Evolution / Ascension system.

Regular waifus (use duplicate copies for stars):
  Total 2 copies  → 1★  (consumes 1 extra)
  Total 5 copies  → 2★  (consumes 4 extra)
  Total 10 copies → 3★  (consumes 9 extra)

Special Edition (spend coins for stars):
  1★ = 1 000 🪙
  2★ = 1 500 🪙
  3★ = 2 000 🪙

Stars stored in user_collection as  waifu_stars: {char_id: N}
"""
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection

# ── Constants ─────────────────────────────────────────────────────────────────

STAR_THRESHOLDS: dict[int, int] = {1: 2, 2: 5, 3: 10}   # copies needed
SPECIAL_COIN_COSTS: dict[int, int] = {1: 1_000, 2: 1_500, 3: 2_000}
SPECIAL_RARITY = "💮 Special Edition"

_PAGE = 6   # waifus shown per page


# ── Helpers ───────────────────────────────────────────────────────────────────

def stars_display(n: int) -> str:
    """Return e.g. '★★☆' for n=2."""
    return "★" * n + "☆" * (3 - n)


def _can_evolve(count: int, current_stars: int, rarity: str) -> list[int]:
    """Return list of star levels achievable given current state."""
    levels = []
    if rarity == SPECIAL_RARITY:
        for s in (1, 2, 3):
            if s > current_stars:
                levels.append(s)
    else:
        for s in (1, 2, 3):
            if s > current_stars and count >= STAR_THRESHOLDS[s]:
                levels.append(s)
    return levels


def _cost_line(rarity: str, star: int, count: int) -> str:
    if rarity == SPECIAL_RARITY:
        return f"{SPECIAL_COIN_COSTS[star]:,} 🪙"
    needed = STAR_THRESHOLDS[star]
    extra  = needed - 1   # copies consumed (keep 1)
    return f"{extra} duplicate card{'s' if extra > 1 else ''} (need {needed} total, have {count})"


# ── Build evolvable list ───────────────────────────────────────────────────────

async def _get_evolvable(user_id: int) -> list[dict]:
    """Return list of {cid, char, count, stars} that can be evolved."""
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
        count   = id_counts[cid]
        stars   = stars_map.get(cid, 0)
        rarity  = char.get("rarity", "")
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
            "<i>Duplicate waifu ပိုရ (သို့) Special Edition ရမှ ဒီ menu မှာ ပေါ်လာမယ်</i>",
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
        badge  = "💮" if rarity == SPECIAL_RARITY else "🔮" if "Mythical" in rarity else "🟡" if "Legendary" in rarity else "🟣" if "Rare" in rarity else "⚪"
        lines.append(
            f"{badge} <b>{escape(c['name'])}</b>  {s_disp}\n"
            f"   ×{it['count']} copies  •  <i>{rarity}</i>"
        )

    text = "\n\n".join(lines)

    # Build buttons (one per evolvable waifu)
    btns = []
    for it in slice_:
        c = it["char"]
        btns.append([InlineKeyboardButton(
            f"{stars_display(it['stars'])} {c['name'][:22]}",
            callback_data=f"evo:view:{it['cid']}:{page}",
        )])

    # Pagination
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

    count = sum(1 for c in chars if c["id"] == cid)
    char  = next((c for c in chars if c["id"] == cid), None)
    if not char:
        await query.answer("Character not found.", show_alert=True)
        return

    stars   = stars_map.get(cid, 0)
    rarity  = char.get("rarity", "")
    levels  = _can_evolve(count, stars, rarity)

    text = (
        f"⚗️ <b>Evolution — {escape(char['name'])}</b>\n\n"
        f"📊 Rarity: {rarity}\n"
        f"🃏 Copies: ×{count}\n"
        f"⭐ Stars: {stars_display(stars)} ({stars}/3)\n"
        f"💰 Coins: {coins:,} 🪙\n\n"
    )

    if not levels:
        text += "✅ Max star (3★) ရောက်ပြီ!"
        btns = [[InlineKeyboardButton("🔙 Back", callback_data=f"evo:list:{back_page}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)
        return

    text += "<b>Upgrade options:</b>\n"
    for s in levels:
        cost = _cost_line(rarity, s, count)
        text += f"\n  {stars_display(s)} → {cost}"

    btns = []
    for s in levels:
        if rarity == SPECIAL_RARITY:
            needed_coins = SPECIAL_COIN_COSTS[s]
            label = f"{'★'*s}{'☆'*(3-s)} {stars_display(s)} — {needed_coins:,} 🪙"
            if coins < needed_coins:
                label = f"❌ {label} (coin မလုံ)"
                btns.append([InlineKeyboardButton(label, callback_data="evo:noop")])
            else:
                btns.append([InlineKeyboardButton(
                    f"⬆️ Upgrade to {stars_display(s)} — {needed_coins:,} 🪙",
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

    # ── Special Edition: deduct coins ────────────────────────────────────────
    if rarity == SPECIAL_RARITY:
        cost = SPECIAL_COIN_COSTS[target_stars]
        if coins < cost:
            await query.answer(f"Coins မလုံ! ({coins:,}/{cost:,} 🪙)", show_alert=True)
            return
        await user_collection.update_one(
            {"id": user_id},
            {
                "$inc": {"coins": -cost},
                "$set": {f"waifu_stars.{cid}": target_stars},
            },
        )
        await query.answer(f"✨ {char['name']} → {stars_display(target_stars)}  (-{cost:,} 🪙)", show_alert=True)

    # ── Regular waifu: remove duplicate copies ────────────────────────────────
    else:
        needed = STAR_THRESHOLDS[target_stars]
        if count < needed:
            await query.answer(f"Copies မလုံ! ({count}/{needed})", show_alert=True)
            return

        # Remove (needed - 1) duplicate entries from characters array
        to_remove = needed - 1
        new_chars = list(chars)
        removed   = 0
        final     = []
        kept_one  = False
        for c in new_chars:
            if c["id"] == cid:
                if not kept_one:
                    final.append(c)   # keep first copy
                    kept_one = True
                elif removed < to_remove:
                    removed += 1      # discard duplicate
                else:
                    final.append(c)   # keep remaining copies beyond needed
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
            f"✨ {char['name']} → {stars_display(target_stars)}  (-{to_remove} card{'s' if to_remove>1 else ''})",
            show_alert=True,
        )

    # Refresh the view
    await _show_view(query, user_id, cid, back_page)


# ── Callback dispatcher ───────────────────────────────────────────────────────

async def _evo_callback(update: Update, context: CallbackContext) -> None:
    q       = update.callback_query
    user_id = q.from_user.id
    data    = q.data  # evo:list:<page> | evo:view:<cid>:<page> | evo:do:<cid>:<stars>:<page> | evo:noop

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
