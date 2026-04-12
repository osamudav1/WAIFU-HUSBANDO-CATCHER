"""
modules/fav.py — /fav <char_id>

Shows character image with Yes/No buttons to set as favourite.
"""
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, collection as waifu_collection


async def fav(update: Update, context: CallbackContext) -> None:
    """
    /fav <char_id>  — prompt to set a character as favourite.
    """
    user   = update.effective_user
    uid    = user.id

    if not context.args:
        await update.message.reply_text(
            "Usage: /fav <character_id>\nExample: /fav 0021",
        )
        return

    char_id = context.args[0].strip()

    # Verify user owns this character
    u_doc = await user_collection.find_one({"id": uid})
    if not u_doc:
        await update.message.reply_text("❌ You haven't caught any characters yet!")
        return

    owned_ids = [c["id"] for c in u_doc.get("characters", [])]
    if char_id not in owned_ids:
        await update.message.reply_text(
            f"❌ You don't own character <code>{escape(char_id)}</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Fetch character details
    char = await waifu_collection.find_one({"id": char_id})
    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{escape(char_id)}</code> not found.",
            parse_mode=ParseMode.HTML,
        )
        return

    name    = escape(char.get("name",  "Unknown"))
    anime   = escape(char.get("anime", "Unknown"))
    rarity  = char.get("rarity", "")
    img_url = char.get("img_url", "")

    # Rarity emoji (first token)
    rar_emoji = rarity.split(" ", 1)[0] if rarity else "🎴"

    caption = (
        f"<b>DO YOU WANT TO SET THIS CHARACTER AS YOUR FAVOURITE?</b>\n\n"
        f"« {name} [{rar_emoji}] ({anime})"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Yes", callback_data=f"fav:set:{uid}:{char_id}"),
        InlineKeyboardButton("🔴 No",  callback_data=f"fav:no:{uid}"),
    ]])

    if img_url:
        try:
            await update.message.reply_photo(
                photo=img_url,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    await update.message.reply_text(
        caption,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def fav_callback(update: Update, context: CallbackContext) -> None:
    q    = update.callback_query
    data = q.data.split(":")

    # Only the intended user can press
    presser_id = q.from_user.id

    if data[1] == "no":
        owner_id = int(data[2])
        if presser_id != owner_id:
            await q.answer("❌ This is not your prompt.", show_alert=True)
            return
        await q.answer("Cancelled.")
        await q.edit_message_caption("❌ Favourite not changed.")
        return

    if data[1] == "set":
        owner_id = int(data[2])
        char_id  = data[3]

        if presser_id != owner_id:
            await q.answer("❌ This is not your prompt.", show_alert=True)
            return

        # Set as primary favourite (prepend to list, deduplicate)
        u_doc = await user_collection.find_one({"id": owner_id})
        favs  = [f for f in (u_doc or {}).get("favorites", []) if f != char_id]
        favs  = [char_id] + favs

        await user_collection.update_one(
            {"id": owner_id},
            {"$set": {"favorites": favs}},
        )

        # Fetch char name for confirmation
        char = await waifu_collection.find_one({"id": char_id})
        name = escape((char or {}).get("name", char_id))

        await q.answer(f"⭐ {name} set as favourite!", show_alert=False)
        await q.edit_message_caption(
            f"⭐ <b>{name}</b> has been set as your favourite character!",
            parse_mode=ParseMode.HTML,
        )


application.add_handler(CommandHandler("fav", fav, block=False))
application.add_handler(
    CallbackQueryHandler(fav_callback, pattern=r"^fav:", block=False)
)
