"""
modules/aichat.py — AI character chat (Asuka Langley Soryu) via Groq.

Rules:
  - Bot name mentioned  → 100% reply
  - Bot name not mentioned → 10% random reply
  - Always replies in Myanmar
  - Ends sentences with နော် or ရှင့်
  - Owner → affectionate; others → Asuka's sharp personality
"""
import os
import random

from groq import AsyncGroq
from telegram import Update
from telegram.ext import CallbackContext, MessageHandler, filters

from waifu import application, LOGGER
from waifu.config import Config

_groq   = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY", ""))
_MODEL  = "llama3-8b-8192"

_BOT_CMD_PATTERN = __import__("re").compile(
    r"/(ping|start|harem|market|sell|buy|delist|trade|daily|balance|"
    r"search|upload|forcedrop|changetime|broadcast|update|delete|duel|"
    r"evolution|leaderboard|profile|stats|sudo|help)\b",
    __import__("re").IGNORECASE,
)

_SYSTEM = """မင်းက Anime ထဲက ချစ်စရာကောင်းတဲ့ မိန်းကလေး 'Asuka Langley Soryu' ဖြစ်တယ်။
စရိုက်: အနည်းငယ်ရှက်တတ်တယ်၊ ဒါပေမယ့် ဖော်ရွေတယ်၊ bot owner ကို အရမ်းခင်တယ်။

ဖြေဆိုပုံ:
- မြန်မာဘာသာနဲ့သာ ပြောရမည်
- သဘာဝကျတဲ့ စကားပြော ပုံစံနဲ့ ဖြေပါ၊ chatting လုပ်သလိုပဲ
- အကြောင်းအရာပေါ်မူတည်ပြီး ၂-၄ ကြောင်း လောက် ဖြေပါ
- ဘာသာပြန်ဆန်ဆန် ပုံကူးထားသလိုမဟုတ်ဘဲ ကောင်မလေးတစ်ယောက် ပြောသလို သဘာဝကျအောင် ပြောပါ
- ဖြေတိုင်း 'နော်' ဒါမှမဟုတ် 'ရှင့်' နဲ့ ဆုံးပါ
- Owner နဲ့ ပြောတာဆိုရင် ချစ်ချစ်ခင်ခင် ဖွင့်ဟပြောဆိုပါ
- Owner မဟုတ်ရင် Asuka ရဲ့ ဇွတ်ကြမ်းပါမယ့် ကောင်မလေး ပုံစံနဲ့ ပြောပါ
- ရိုင်းစိုင်းတဲ့ မေးခွန်းကို ပြင်းပြင်းထန်ထန် ပြန်တုံ့ပြန်ပါ
- Bot commands (/ping, /market, /sell, /trade, /harem, /daily စသည်) နဲ့ bot အလုပ်လုပ်ပုံနဲ့ ပတ်သက်တာ လုံးဝ မဆွေးနွေးနဲ့၊ သိဟန်မဆောင်နဲ့"""


async def _ask_groq(user_msg: str, is_owner: bool) -> str:
    role_hint = (
        "ဒီသူကတော့ owner ဖြစ်တယ်၊ သူ့ကို အချစ်နဲ့ ချစ်ချစ်ခင်ခင် ပြောပေး နော်။"
        if is_owner else
        "ဒီသူကတော့ owner မဟုတ်ဘူး၊ Asuka ရဲ့ ပုံစံစရိုက်အတိုင်း ပြောပေး ရှင့်။"
    )
    try:
        resp = await _groq.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system",  "content": _SYSTEM},
                {"role": "system",  "content": role_hint},
                {"role": "user",    "content": user_msg},
            ],
            max_tokens=400,
            temperature=0.9,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        LOGGER.error("Groq error: %s", e)
        return "ဘာပြောမှန်းမသိဘူး နော်… နောက်မှ ပြောပါ ရှင့်။"


async def ai_chat(update: Update, context: CallbackContext) -> None:
    msg  = update.effective_message
    text = msg.text or ""
    uid  = update.effective_user.id if update.effective_user else 0

    # Skip messages that talk about bot commands / bot functionality
    if _BOT_CMD_PATTERN.search(text):
        return

    dollar_trigger = text.startswith("$")

    bot_username = Config.BOT_USERNAME.lstrip("@").lower()
    mentioned    = bot_username in text.lower() or (
        msg.reply_to_message and msg.reply_to_message.from_user and
        msg.reply_to_message.from_user.username and
        msg.reply_to_message.from_user.username.lower() == bot_username
    )

    # $ prefix → 100% reply; mention → 100%; else 10% random
    if not dollar_trigger and not mentioned and random.random() > 0.10:
        return

    # Strip leading $ for cleaner prompt
    if dollar_trigger:
        text = text[1:].strip()

    is_owner = (uid == Config.OWNER_ID)

    # Strip bot username mention from text for cleaner prompt
    clean = text.replace(f"@{Config.BOT_USERNAME.lstrip('@')}", "").strip()
    if not clean:
        clean = "ဟေး"

    reply = await _ask_groq(clean, is_owner)

    try:
        await msg.reply_text(reply)
    except Exception as e:
        LOGGER.error("AI chat reply error: %s", e)


# Listen in groups, supergroups AND private DMs (not commands)
application.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & (
            filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.PRIVATE
        ),
        ai_chat,
        block=False,
    )
)
