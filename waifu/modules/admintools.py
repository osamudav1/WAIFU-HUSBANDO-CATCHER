"""
modules/admintools.py — Owner-only admin tools.

/coins <user_id> +<amount>   — add coins to a user
/coins <user_id> -<amount>   — subtract coins from a user

Only works in owner's private DM.
"""
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection, OWNER_ID


def _is_owner_pm(update: Update) -> bool:
    return (
        update.effective_user.id == OWNER_ID
        and update.effective_chat.type == "private"
    )


async def coins_cmd(update: Update, context: CallbackContext) -> None:
    if not _is_owner_pm(update):
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /coins <user_id> +<amount> | -<amount>\n"
            "Example: /coins 123456789 +500\n"
            "         /coins 123456789 -200"
        )
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id.")
        return

    raw = args[1].strip()
    if raw.startswith("+"):
        sign = 1
        raw  = raw[1:]
    elif raw.startswith("-"):
        sign = -1
        raw  = raw[1:]
    else:
        sign = 1

    try:
        amount = int(raw)
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        return

    delta = sign * amount

    target = await user_collection.find_one({"id": target_id})
    if not target:
        await update.message.reply_text(f"❌ User <code>{target_id}</code> not found.", parse_mode=ParseMode.HTML)
        return

    await user_collection.update_one(
        {"id": target_id},
        {"$inc": {"coins": delta}},
    )

    updated = await user_collection.find_one({"id": target_id}, {"coins": 1})
    new_bal = (updated or {}).get("coins", 0)
    fn      = escape(target.get("first_name", str(target_id)))

    sign_sym = "+" if delta >= 0 else ""
    await update.message.reply_text(
        f"✅ <b>{fn}</b> (<code>{target_id}</code>)\n"
        f"💰 Coins: {sign_sym}{delta:,}  →  Balance: <b>{new_bal:,} 🪙</b>",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("coins", coins_cmd, block=False))
