# Waifu Catcher Bot

A Telegram-based anime character collection game bot.

## Overview

Users can "catch" anime characters (waifus/husbandos) that are automatically dropped in Telegram groups, build a collection (harem), trade with other users, battle in duels, and participate in a virtual economy.

## Tech Stack

- **Language**: Python 3.10+
- **Telegram Framework**: python-telegram-bot v22.7 (async polling, colored buttons)
- **Database**: MongoDB via Motor (async driver) with optimized indexes
- **Caching**: cachetools TTLCache (user docs 30s, char list 120s, chat config 5m)
- **Concurrency**: asyncio.Semaphore (DB rate limit) + per-user asyncio.Lock (message queue)
- **Scheduling**: APScheduler for timed character drops
- **Config**: python-dotenv for environment variable loading

## Project Structure

```
waifu/
├── __init__.py       # Shared singletons: DB, bot application, collections
├── __main__.py       # Entry point — run with `python -m waifu`
├── config.py         # Environment variable parsing
└── modules/          # Feature handlers
    ├── waifu_drop.py  # Core game loop (drops + /guess)
    ├── start.py       # /start, help panel
    ├── harem.py       # Paginated collection view
    ├── profile.py     # User stats, XP, levels
    ├── economy.py     # Coins, /daily, marketplace
    ├── duel.py        # PvP battle system
    ├── trade.py       # Character trading & gifting
    ├── upload.py      # Sudo tools for adding characters
    ├── leaderboard.py # Rankings
    ├── inlinequery.py # Inline catalogue search
    ├── broadcast.py   # Owner broadcast
    ├── changetime.py  # Per-group drop frequency
    ├── ping.py        # Latency + uptime
    └── eval.py        # Dev REPL
```

## Running

```bash
python -m waifu
```

## Required Environment Variables (Secrets)

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `BOT_USERNAME` | Bot username without @ |
| `OWNER_ID` | Owner's Telegram user ID |
| `MONGO_URI` | MongoDB connection string |
| `GROUP_ID` | Telegram group ID for notifications |
| `CHARA_CHANNEL_ID` | Telegram channel ID for character cards |

## Optional Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SUDO_IDS` | — | Comma-separated extra sudo user IDs |
| `DB_NAME` | `waifu_bot` | MongoDB database name |
| `SUPPORT_CHAT` | — | Support group username |
| `UPDATE_CHAT` | — | Updates channel username |
| `PHOTO_URLS` | — | Comma-separated fallback photo URLs |
| `DROP_INTERVAL_MINUTES` | `15` | Timed auto-drop interval |
| `DEFAULT_MSG_FREQUENCY` | `100` | Messages before threshold drop |

## Workflow

- **Start application**: `python -m waifu` (console output)
