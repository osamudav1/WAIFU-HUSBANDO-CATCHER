"""
modules/duel.py — PvP duel system.

Flow:
  User A: /duel (reply to User B)
  → Bot shows User A's top-5 characters as buttons
  → User A picks one
  → Bot shows User B's top-5 characters as buttons
  → User B picks one
  → Stats-based battle → winner gets coins + XP
"""
import asyncio
import random
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection
from waifu.config import Config

# pending duels:  duel_id → {...}
_pending: dict[str, dict] = {}
_EXPIRY = 120   # 2 minutes

_RARITY_POWER = {
    "⚪ Common": 1, "🟢 Medium": 2, "🟣 Rare": 3,
    "🟡 Legendary": 4, "💮 Special Edition": 5,
}


def _duel_id(a: int, b: int) -> str:
    return f"duel_{a}_{b}_{int(time.time())}"


async def _expire(did: str) -> None:
    await asyncio.sleep(_EXPIRY)
    _pending.pop(did, None)


def _power(char: dict) -> int:
    base  = _RARITY_POWER.get(char.get("rarity", ""), 1)
    return base * random.randint(80, 120)  # add variance


async def duel(update: Update, context: CallbackContext) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Reply to a user's message to duel them!\nUsage: /duel (reply)")
        return

    a       = update.effective_user
    b       = update.message.reply_to_message.from_user
    if a.id == b.id:
        await update.message.reply_text("❌ You can't duel yourself!")
        return
    if b.is_bot:
        await update.message.reply_text("❌ You can't duel a bot!")
        return

    a_doc = await user_collection.find_one({"id": a.id})
    b_doc = await user_collection.find_one({"id": b.id})
    if not a_doc or not a_doc.get("characters"):
        await update.message.reply_text("❌ You need at least one character to duel!")
        return
    if not b_doc or not b_doc.get("characters"):
        await update.message.reply_text(
            f"❌ {escape(b.first_name)} has no characters yet!", parse_mode=ParseMode.HTML)
        return

    did = _duel_id(a.id, b.id)

    # Pick top-5 unique characters for challenger
    unique_a = list({c["id"]: c for c in a_doc["characters"]}.values())[:5]

    _pending[did] = {
        "challenger_id":   a.id,
        "challenger_name": a.first_name,
        "opponent_id":     b.id,
        "opponent_name":   b.first_name,
        "a_chars":         unique_a,
        "b_chars":         list({c["id"]: c for c in b_doc["characters"]}.values())[:5],
        "a_pick":          None,
        "b_pick":          None,
    }
    asyncio.create_task(_expire(did))

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{c.get('rarity','🎴')} {c['name']}",
            callback_data=f"duel_pick_a:{did}:{i}",
        )]
        for i, c in enumerate(unique_a)
    ])
    await update.message.reply_text(
        f"⚔️ <b>{escape(a.first_name)}</b> challenged <b>{escape(b.first_name)}</b>!\n\n"
        f"{escape(a.first_name)}, pick your fighter:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def duel_pick_a(update: Update, context: CallbackContext) -> None:
    q      = update.callback_query
    parts  = q.data.split(":")
    did, idx = parts[1], int(parts[2])

    state = _pending.get(did)
    if not state:
        await q.answer("⌛ Duel expired!", show_alert=True); return
    if q.from_user.id != state["challenger_id"]:
        await q.answer("❌ Not your pick!", show_alert=True); return

    await q.answer()
    state["a_pick"] = state["a_chars"][idx]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{c.get('rarity','🎴')} {c['name']}",
            callback_data=f"duel_pick_b:{did}:{i}",
        )]
        for i, c in enumerate(state["b_chars"])
    ])
    await q.edit_message_text(
        f"⚔️ <b>{escape(state['challenger_name'])}</b> picked "
        f"<b>{escape(state['a_pick']['name'])}</b>!\n\n"
        f"<b>{escape(state['opponent_name'])}</b>, choose your fighter:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def duel_pick_b(update: Update, context: CallbackContext) -> None:
    q      = update.callback_query
    parts  = q.data.split(":")
    did, idx = parts[1], int(parts[2])

    state = _pending.get(did)
    if not state:
        await q.answer("⌛ Duel expired!", show_alert=True); return
    if q.from_user.id != state["opponent_id"]:
        await q.answer("❌ Not your pick!", show_alert=True); return
    if state["a_pick"] is None:
        await q.answer("⌛ Waiting for challenger…", show_alert=True); return

    await q.answer()
    _pending.pop(did, None)

    state["b_pick"] = state["b_chars"][idx]
    a_char = state["a_pick"]
    b_char = state["b_pick"]

    a_power = _power(a_char)
    b_power = _power(b_char)

    if a_power >= b_power:
        winner_id   = state["challenger_id"]
        loser_id    = state["opponent_id"]
        winner_name = state["challenger_name"]
        loser_name  = state["opponent_name"]
        win_char    = a_char["name"]
        lose_char   = b_char["name"]
    else:
        winner_id   = state["opponent_id"]
        loser_id    = state["challenger_id"]
        winner_name = state["opponent_name"]
        loser_name  = state["challenger_name"]
        win_char    = b_char["name"]
        lose_char   = a_char["name"]

    # Update coins + XP + wins
    await user_collection.update_one(
        {"id": winner_id},
        {"$inc": {"coins": Config.DUEL_WIN_COINS, "xp": 100, "wins": 1}},
    )
    await user_collection.update_one(
        {"id": loser_id},
        {"$inc": {"coins": Config.DUEL_LOSE_COINS, "xp": 25}},
    )

    await q.edit_message_text(
        f"⚔️ <b>Duel Result!</b>\n\n"
        f"🏆 <b>{escape(winner_name)}</b> won with <b>{escape(win_char)}</b>!\n"
        f"   Power: <b>{max(a_power, b_power)}</b>\n\n"
        f"💀 <b>{escape(loser_name)}</b> lost with <b>{escape(lose_char)}</b>.\n"
        f"   Power: <b>{min(a_power, b_power)}</b>\n\n"
        f"🏅 Winner earns <b>{Config.DUEL_WIN_COINS} 🪙</b> + 100 XP\n"
        f"🎖️ Loser earns <b>{Config.DUEL_LOSE_COINS} 🪙</b> + 25 XP",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("duel", duel, block=False))
application.add_handler(CallbackQueryHandler(duel_pick_a, pattern=r"^duel_pick_a:", block=False))
application.add_handler(CallbackQueryHandler(duel_pick_b, pattern=r"^duel_pick_b:", block=False))
