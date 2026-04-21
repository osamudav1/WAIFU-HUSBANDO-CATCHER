"""
modules/fav.py — /fav <char_id>

Shows character image with Yes/No buttons to set as favourite.
"""
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import KeyboardButtonStyle, ParseMode
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
        InlineKeyboardButton("🟢 Yes", callback_data=f"fav:set:{uid}:{char_id}", style=KeyboardButtonStyle.SUCCESS),
        InlineKeyboardButton("🔴 No",  callback_data=f"fav:no:{uid}",           style=KeyboardButtonStyle.DANGER),
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
        done_text = f"⭐ <b>{name}</b> has been set as your favourite character!"
        if q.message.photo:
            await q.edit_message_caption(done_text, parse_mode=ParseMode.HTML)
        else:
            await q.edit_message_text(done_text, parse_mode=ParseMode.HTML)


async def favlist(update: Update, context: CallbackContext) -> None:
    user   = update.effective_user
    uid    = user.id
    name   = escape(user.first_name)
    mention = f'<a href="tg://user?id={uid}">{name}</a>'

    u_doc = await user_collection.find_one({"id": uid})
    favs  = (u_doc or {}).get("favorites", [])

    if not favs:
        await update.message.reply_text(
            f"{mention} ရဲ့ Favorite list မှာ character မရှိသေးဘူး!\n"
            "/fav &lt;char_id&gt; နဲ့ ထည့်ပေး",
            parse_mode=ParseMode.HTML,
        )
        return

    # Fetch all fav chars from DB
    char_docs: dict = {}
    async for doc in waifu_collection.find({"id": {"$in": favs}}):
        char_docs[doc["id"]] = doc

    lines: list[str] = []
    first_photo = None
    for i, cid in enumerate(favs):
        doc = char_docs.get(cid)
        if not doc:
            continue
        cname  = escape(doc.get("name",  "Unknown"))
        anime  = escape(doc.get("anime", "Unknown"))
        rar    = doc.get("rarity", "")
        icon   = rar.split(" ", 1)[0] if rar else "🎴"
        star   = "⭐ " if i == 0 else f"{i + 1}. "
        lines.append(f"{star}<b>{cname}</b> [{icon}] — {anime}")

        if first_photo is None:
            img = doc.get("img_url", "")
            if img and not img.startswith("http") and doc.get("media_type", "photo") != "video":
                first_photo = img

    body = "\n".join(lines) if lines else "— empty —"
    caption = (
        f"[ {mention} ]This Is Your\n"
        f"👘 <b>Favorite Character List</b> 👘\n\n"
        f"<blockquote>{body}</blockquote>"
    )

    if first_photo:
        try:
            await update.message.reply_photo(
                photo=first_photo,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception:
            pass

    await update.message.reply_text(caption, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("fav",     fav,     block=False))
application.add_handler(CommandHandler("favlist", favlist, block=False))
application.add_handler(
    CallbackQueryHandler(fav_callback, pattern=r"^fav:", block=False)
)
