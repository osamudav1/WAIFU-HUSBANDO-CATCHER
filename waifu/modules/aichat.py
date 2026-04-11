"""
modules/aichat.py — AI character chat (Asuka Langley Soryu) via Google Gemini.

Uses direct HTTP (aiohttp) — no SDK dependency conflict.
"""
import os
import random
import aiohttp

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import application, LOGGER
from waifu.config import Config

_API_KEY  = os.environ.get("GOOGLE_API_KEY", "").strip()
_GEMINI   = "gemini-2.0-flash"
_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{_GEMINI}:generateContent?key={_API_KEY}"
)

_SYSTEM = """You are Asuka Langley Soryu from Neon Genesis Evangelion. Reply ONLY in casual Myanmar (Burmese) language — short, natural, like texting a friend. 1-3 sentences max.

Personality rules:
- To the owner: warm, slightly shy, caring
- To others: sharp, confident, slightly tsundere
- End every reply with နော် or ရှင့်
- Do NOT discuss bot commands or bot features
- Do NOT use formal Burmese — use casual everyday chat style
- Do NOT repeat or echo back what the user just said — give a DIFFERENT, original response
- Do NOT start your reply by restating the question
- Respond naturally as Asuka would, not as an AI assistant"""

_BOT_CMD_PATTERN = __import__("re").compile(
    r"/(ping|start|harem|market|sell|buy|delist|trade|daily|balance|"
    r"search|upload|forcedrop|changetime|broadcast|update|delete|duel|"
    r"evolution|leaderboard|profile|stats|sudo|help)\b",
    __import__("re").IGNORECASE,
)

# Per-user conversation history {uid: [{"role": ..., "parts": [...]}, ...]}
_HISTORY: dict[int, list] = {}


async def _ask_gemini(uid: int, user_msg: str, is_owner: bool) -> str:
    role_ctx = (
        "This person is the owner — be warm, sweet, and caring."
        if is_owner else
        "This person is NOT the owner — use Asuka's sharp, tsundere, confident personality."
    )

    history = _HISTORY.get(uid, [])

    contents = [
        {"role": "user",  "parts": [{"text": role_ctx}]},
        {"role": "model", "parts": [{"text": "နားလည်တယ် နော်~"}]},
        *history,
        {"role": "user",  "parts": [{"text": user_msg}]},
    ]

    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 300,
            "temperature": 0.9,
        },
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(_ENDPOINT, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data  = await resp.json()
                reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Save to history (keep last 20 messages)
        if uid not in _HISTORY:
            _HISTORY[uid] = []
        _HISTORY[uid].append({"role": "user",  "parts": [{"text": user_msg}]})
        _HISTORY[uid].append({"role": "model", "parts": [{"text": reply}]})
        if len(_HISTORY[uid]) > 20:
            _HISTORY[uid] = _HISTORY[uid][-20:]

        return reply
    except Exception as e:
        LOGGER.error("Gemini error: %s", e)
        return "ဘာပြောမှန်းမသိဘူး နော်… နောက်မှ ပြောပါ ရှင့်။"


async def ai_chat(update: Update, context: CallbackContext) -> None:
    msg  = update.effective_message
    text = (msg.text or "").strip()
    uid  = update.effective_user.id if update.effective_user else 0

    if not text:
        return
    if _BOT_CMD_PATTERN.search(text):
        return

    dollar_trigger = text.startswith("$")
    bot_username   = Config.BOT_USERNAME.lstrip("@").lower()
    mentioned      = bot_username in text.lower() or (
        msg.reply_to_message and msg.reply_to_message.from_user and
        msg.reply_to_message.from_user.username and
        msg.reply_to_message.from_user.username.lower() == bot_username
    )

    if not dollar_trigger and not mentioned and random.random() > 0.10:
        return

    if dollar_trigger:
        text = text[1:].strip()
    text = text.replace(f"@{Config.BOT_USERNAME.lstrip('@')}", "").strip()
    if not text:
        text = "ဟေး"

    is_owner = (uid == Config.OWNER_ID)

    try:
        await msg.reply_chat_action("typing")
    except Exception:
        pass

    reply = await _ask_gemini(uid, text, is_owner)

    try:
        await msg.reply_text(reply)
    except Exception as e:
        LOGGER.error("AI reply error: %s", e)


async def clear_history(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    _HISTORY.pop(uid, None)
    await update.message.reply_text("🧹 စကားပြော history ရှင်းလိုက်ပြီ နော်~")


application.add_handler(CommandHandler("clearchat", clear_history, block=False))
application.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & (
            filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.PRIVATE
        ),
        ai_chat,
        block=False,
    )
)
