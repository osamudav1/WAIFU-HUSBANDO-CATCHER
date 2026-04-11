"""
modules/aichat.py — AI character chat (Asuka Langley Soryu) via Groq.

Features:
  - Conversation history per user (ChatGPT/Gemini style multi-turn)
  - $ prefix → 100% reply
  - Bot mention → 100% reply
  - No mention → 10% random
  - Never discusses bot commands/functionality
"""
import os
import random
from collections import defaultdict, deque

from groq import AsyncGroq
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import application, LOGGER
from waifu.config import Config

def _get_groq_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    # Fix iOS autocorrect capitalizing first letter (Gsk_ → gsk_)
    if key.lower().startswith("gsk_"):
        key = "gsk_" + key[4:]
    return key

_groq  = AsyncGroq(api_key=_get_groq_key())
_MODEL = "llama-3.3-70b-versatile"

# Conversation history: {user_id: deque([{role, content}, ...])}
# Keep last 20 turns (10 user + 10 assistant) per user
_HISTORY: dict[int, deque] = defaultdict(lambda: deque(maxlen=20))

_BOT_CMD_PATTERN = __import__("re").compile(
    r"/(ping|start|harem|market|sell|buy|delist|trade|daily|balance|"
    r"search|upload|forcedrop|changetime|broadcast|update|delete|duel|"
    r"evolution|leaderboard|profile|stats|sudo|help)\b",
    __import__("re").IGNORECASE,
)

_SYSTEM = """မင်းက Anime ထဲက ချစ်စရာကောင်းတဲ့ မိန်းကလေး 'Asuka Langley Soryu' ဖြစ်တယ်။
စရိုက်: အနည်းငယ်ရှက်တတ်တယ်၊ ဒါပေမယ့်ဖော်ရွေတယ်၊ bot owner ကို အရမ်းခင်တယ်။

ဖြေဆိုပုံ:
- မြန်မာဘာသာနဲ့သာ ပြောရမည်
- ChatGPT / Gemini လို သဘာဝကျကျ စကားပြောပုံစံနဲ့ ဆက်သွယ်ပါ
- အရင်ပြောခဲ့တဲ့ conversation ကို မှတ်မိပြီး ဆက်စပ်ပြောပါ
- ၂-၄ ကြောင်းဖြေပါ၊ တိုလွန်းလည်း မဖြေနဲ့၊ ရှည်လွန်းလည်း မဖြေနဲ့
- ဘာသာပြန်ဆန်ဆန် မဟုတ်ဘဲ ကောင်မလေးတစ်ယောက် chatting လုပ်သလို ဖြေပါ
- ဖြေတိုင်း 'နော်' သို့မဟုတ် 'ရှင့်' နဲ့ ဆုံးပါ
- Owner နဲ့ဆိုရင် ချစ်ချစ်ခင်ခင် ဖွင့်ဟပြောဆိုပါ
- Owner မဟုတ်ရင် Asuka ရဲ့ ဇွတ်ကြမ်းတဲ့ personality နဲ့ ပြောပါ
- ရိုင်းစိုင်းတဲ့ မေးခွန်းကို ပြင်းပြင်းထန်ထန် ပြန်တုံ့ပြန်ပါ
- Bot commands (/ping /market /sell /trade /harem /daily etc) နဲ့ bot function ပတ်သက်တာ လုံးဝ မပြောနဲ့"""


async def _ask_groq(uid: int, user_msg: str, is_owner: bool) -> str:
    role_hint = (
        "ဒီသူကတော့ owner ဖြစ်တယ်၊ ချစ်ချစ်ခင်ခင် ပြောပေး နော်။"
        if is_owner else
        "ဒီသူကတော့ owner မဟုတ်ဘူး၊ Asuka ပုံစံအတိုင်း ပြောပေး ရှင့်။"
    )

    # Build message list: system → role_hint → history → new user msg
    history   = list(_HISTORY[uid])
    messages  = [
        {"role": "system", "content": _SYSTEM},
        {"role": "system", "content": role_hint},
        *history,
        {"role": "user",   "content": user_msg},
    ]

    try:
        resp = await _groq.chat.completions.create(
            model=_MODEL,
            messages=messages,
            max_tokens=500,
            temperature=0.9,
        )
        reply = resp.choices[0].message.content.strip()

        # Save to history
        _HISTORY[uid].append({"role": "user",      "content": user_msg})
        _HISTORY[uid].append({"role": "assistant",  "content": reply})

        return reply
    except Exception as e:
        LOGGER.error("Groq error: %s", e)
        return "ဘာပြောမှန်းမသိဘူး နော်… နောက်မှ ပြောပါ ရှင့်။"


async def ai_chat(update: Update, context: CallbackContext) -> None:
    msg  = update.effective_message
    text = (msg.text or "").strip()
    uid  = update.effective_user.id if update.effective_user else 0

    if not text:
        return

    # Skip bot command discussions
    if _BOT_CMD_PATTERN.search(text):
        return

    dollar_trigger = text.startswith("$")

    bot_username = Config.BOT_USERNAME.lstrip("@").lower()
    mentioned    = bot_username in text.lower() or (
        msg.reply_to_message and msg.reply_to_message.from_user and
        msg.reply_to_message.from_user.username and
        msg.reply_to_message.from_user.username.lower() == bot_username
    )

    # Decide whether to reply
    if not dollar_trigger and not mentioned and random.random() > 0.10:
        return

    # Clean up trigger prefix / mention
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

    reply = await _ask_groq(uid, text, is_owner)

    try:
        await msg.reply_text(reply)
    except Exception as e:
        LOGGER.error("AI chat reply error: %s", e)


async def clear_history(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    _HISTORY[uid].clear()
    await update.message.reply_text("🧹 စကားပြော history ရှင်းလိုက်ပြီ နော်~")


# Handlers
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
