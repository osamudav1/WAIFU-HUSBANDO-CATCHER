import glob
import logging
import os
import sys
import time
from pathlib import Path

if sys.version_info < (3, 10):
    import sys; sys.stderr.write("ERROR: Python 3.10+ required.\n")
    sys.exit(1)

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    level=logging.INFO,
)
for _lib in ("apscheduler", "httpx", "telegram.ext", "pymongo", "motor"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

LOGGER    = logging.getLogger("waifu")
StartTime = time.time()

from waifu.config import Config

TOKEN            = Config.TOKEN
BOT_USERNAME     = Config.BOT_USERNAME
OWNER_ID: int    = Config.OWNER_ID
sudo_users: set[int] = Config.all_sudo()
DEV_LIST: set[int] = sudo_users   # alias for eval.py
GROUP_ID         = Config.GROUP_ID
CHARA_CHANNEL_ID = Config.CHARA_CHANNEL_ID
PHOTO_URL        = Config.PHOTO_URL
SUPPORT_CHAT     = Config.SUPPORT_CHAT
UPDATE_CHAT      = Config.UPDATE_CHAT

from waifu.memdb import MemDatabase

db = MemDatabase()
LOGGER.info("Using in-memory storage (data resets on restart).")

collection                   = db["anime_characters"]
user_collection              = db["users"]
user_totals_collection       = db["chat_settings"]
group_user_totals_collection = db["group_user_totals"]
top_global_groups_collection = db["top_groups"]
pm_users                     = db["pm_users"]
market_collection            = db["market_listings"]
bm_market_collection         = db["bm_market"]
star_market_collection       = db["star_market"]
ton_orders_collection        = db["ton_orders"]
active_drops_collection      = db["active_drops"]
bot_settings_collection      = db["bot_settings"]
waifu_collection             = collection

from telegram.ext import Application
from telegram.request import HTTPXRequest

_request = HTTPXRequest(
    connect_timeout=60,
    read_timeout=60,
    write_timeout=60,
    pool_timeout=60,
    http_version="1.1",
)

application: Application = (
    Application.builder()
    .token(TOKEN)
    .request(_request)
    .concurrent_updates(True)
    .build()
)

# Module loader
_LOAD    = [x.strip() for x in os.environ.get("LOAD_MODULES",    "").split(",") if x.strip()]
_NO_LOAD = [x.strip() for x in os.environ.get("NO_LOAD_MODULES", "").split(",") if x.strip()]


def _list_all_modules() -> list[str]:
    mod_dir = Path(__file__).parent / "modules"
    mods = sorted(
        Path(f).stem
        for f in glob.glob(str(mod_dir / "*.py"))
        if not Path(f).name.startswith("_")
    )
    if _LOAD:
        bad = set(_LOAD) - set(mods)
        if bad:
            LOGGER.error("Unknown LOAD_MODULES: %s", bad)
            sys.exit(1)
        mods = [m for m in mods if m not in _LOAD] + _LOAD
    if _NO_LOAD:
        mods = [m for m in mods if m not in _NO_LOAD]
    return mods


ALL_MODULES = _list_all_modules()
LOGGER.info("Modules queued: %s", ALL_MODULES)

registered_chats: set[int] = set()   # all group IDs ever seen (shared across modules)

__all__ = [
    "ALL_MODULES", "application", "db", "LOGGER", "StartTime",
    "OWNER_ID", "sudo_users", "DEV_LIST", "TOKEN", "BOT_USERNAME",
    "GROUP_ID", "CHARA_CHANNEL_ID", "PHOTO_URL", "SUPPORT_CHAT", "UPDATE_CHAT",
    "collection", "user_collection", "user_totals_collection",
    "group_user_totals_collection", "top_global_groups_collection",
    "pm_users", "market_collection", "bm_market_collection",
    "star_market_collection", "ton_orders_collection", "active_drops_collection",
    "bot_settings_collection", "waifu_collection",
    "registered_chats",
]
