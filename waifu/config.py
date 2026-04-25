import os

# ---------------------------------------------------------------------------
# Platform detection — load .env only in local dev.
# HuggingFace / Replit / Fly / Koyeb / Render all inject secrets into
# os.environ automatically, so python-dotenv must NOT override them.
# ---------------------------------------------------------------------------
_is_platform = any(os.environ.get(k) for k in (
    "SPACE_ID",        # HuggingFace Spaces
    "REPLIT_DOMAINS",  # Replit  (dev + deployed)
    "FLY_APP_NAME",    # Fly.io
    "KOYEB_APP_NAME",  # Koyeb
    "RENDER",          # Render
))

if not _is_platform:
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _require(key: str) -> str:
    value = _get(key)
    if not value:
        raise RuntimeError(
            f"[Config] Required secret '{key}' is not set.\n"
            f"  • HuggingFace : Space Settings → Variables and Secrets → add '{key}'\n"
            f"  • Replit      : Secrets panel → add '{key}'\n"
            f"  • Local dev   : copy .env.example → .env and fill it in"
        )
    return value


def _int_list(key: str) -> list[int]:
    return [
        int(x.strip())
        for x in _get(key).split(",")
        if x.strip().lstrip("-").isdigit()
    ]


# ---------------------------------------------------------------------------
# Config class — every sensitive value comes from os.environ.get()
# ---------------------------------------------------------------------------

class Config:

    # ── Required secrets ────────────────────────────────────────────────────
    TOKEN:            str       = os.environ.get("BOT_TOKEN",        "").strip()
    OWNER_ID:         int       = int(os.environ.get("OWNER_ID",     "0").strip() or "0")
    GROUP_ID:         int       = int(os.environ.get("GROUP_ID",     "0").strip() or "0")
    CHARA_CHANNEL_ID: int       = int(os.environ.get("CHARA_CHANNEL_ID", "0").strip() or "0")
    mongo_url:        str       = os.environ.get("MONGO_URI",        "").strip()

    # ── Optional secrets / settings ─────────────────────────────────────────
    BOT_USERNAME:     str       = os.environ.get("BOT_USERNAME",     "").strip().lstrip("@")
    sudo_users:       list[int] = _int_list("SUDO_IDS")
    DB_NAME:          str       = os.environ.get("DB_NAME",          "waifu_bot").strip()
    SUPPORT_CHAT:     str       = os.environ.get("SUPPORT_CHAT",     "").strip()
    UPDATE_CHAT:      str       = os.environ.get("UPDATE_CHAT",      "").strip()
    PHOTO_URL:        list[str] = [
        u.strip()
        for u in os.environ.get("PHOTO_URLS", "").split(",")
        if u.strip().startswith("http")
    ]
    DROP_INTERVAL_MIN:     int  = int(os.environ.get("DROP_INTERVAL_MINUTES",  "15")  or "15")
    DEFAULT_MSG_FREQUENCY: int  = int(os.environ.get("DEFAULT_MSG_FREQUENCY",  "100") or "100")
    FILE_STORE_CHAT_ID:    int  = int(os.environ.get("FILE_STORE_CHAT_ID",     "0")   or "0")

    # ── Economy constants ────────────────────────────────────────────────────
    DAILY_COINS:     int = 100
    DUEL_WIN_COINS:  int = 150
    DUEL_LOSE_COINS: int = 30

    # ── Rarity map ───────────────────────────────────────────────────────────
    RARITY_MAP: dict[int, str] = {
        1:  "⚪ Common",
        2:  "🟣 Rare",
        3:  "🟤 Medium",
        4:  "🟡 Legendary",
        5:  "🔮 Mythical",
        9:  "🪞 Supreme",
        6:  "💮 Special Edition",
        7:  "🌐 Global",
        10: "✖️ CrossVerse",
        8:  "🌌 Universal",
    }

    @classmethod
    def all_sudo(cls) -> set[int]:
        return {cls.OWNER_ID, *cls.sudo_users}

    @classmethod
    def validate(cls) -> None:
        """Call once at startup to catch missing required secrets early."""
        missing = []
        if not cls.TOKEN:            missing.append("BOT_TOKEN")
        if not cls.OWNER_ID:         missing.append("OWNER_ID")
        if not cls.GROUP_ID:         missing.append("GROUP_ID")
        if not cls.CHARA_CHANNEL_ID: missing.append("CHARA_CHANNEL_ID")
        if not cls.mongo_url:        missing.append("MONGO_URI")
        if missing:
            raise RuntimeError(
                f"[Config] Missing required secrets: {', '.join(missing)}\n"
                f"  HuggingFace → Space Settings → Variables and Secrets\n"
                f"  Replit      → Secrets panel"
            )
