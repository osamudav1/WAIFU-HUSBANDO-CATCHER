<div align="center">

<img src="https://i.ibb.co/gbXCJDZy/download-73.jpg" alt="Waifu Catcher Banner" width="400" style="border-radius: 16px;" />

<h1>🌸 Waifu Catcher Bot</h1>

<p><em>A fully-featured anime character collection bot for Telegram<br>check the bot <a href="https://t.me/OtakuFlix_post_bot">ᴡᴀɪғᴜ ɢʀᴀʙʙᴇʀ ʙᴏᴛ</a></em></p>

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-20.x-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://python-telegram-bot.org)
[![MongoDB](https://img.shields.io/badge/MongoDB-Motor%20Async-47A248?style=for-the-badge&logo=mongodb&logoColor=white)](https://motor.readthedocs.io)
[![APScheduler](https://img.shields.io/badge/APScheduler-3.x-FF6B6B?style=for-the-badge&logo=clockify&logoColor=white)](https://apscheduler.readthedocs.io)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)
[![Stars](https://img.shields.io/github/stars/MyNameIsShekhar/WAIFU-HUSBANDO-CATCHER?style=for-the-badge&color=FFD700&logo=github)](https://github.com/MyNameIsShekhar/WAIFU-HUSBANDO-CATCHER/stargazers)

</div>

---

## ✨ Feature Overview

| Module | Commands | Description |
|--------|----------|-------------|
| 🎲 `waifu_drop` | `/guess` `/fav` | Auto-drops in groups — timed + message threshold |
| 🚀 `start` | `/start` | Welcome screen with inline help panel |
| 📚 `harem` | `/harem` `/collection` | Paginated collection grouped by anime |
| 👤 `profile` | `/profile` | Level, XP bar, rarity breakdown, collection value |
| 💰 `economy` | `/daily` `/balance` `/sell` `/market` `/buy` `/delist` | Coins, daily reward, marketplace |
| ⚔️ `duel` | `/duel` | PvP inline-button character battles |
| 🔄 `trade` | `/trade` `/gift` | Trade or gift characters between users |
| 📤 `upload` | `/upload` `/uploadchar` `/delete` `/update` | Two upload methods |
| 🏆 `leaderboard` | `/top` `/ctop` `/TopGroups` `/stats` | Global and per-group rankings |
| 🔍 `inlinequery` | — | Inline search of full catalogue or personal collection |
| 📢 `broadcast` | `/broadcast` | Rate-limited owner broadcast |
| ⚙️ `changetime` | `/changetime` `/resettime` | Per-group drop frequency (admins) |
| 🏓 `ping` | `/ping` | Latency + uptime (sudo) |
| 🛠️ `eval` | `/e` `/py` `/sh` `/clearlocals` | Dev REPL (DEV_LIST only) |

---

## 🚀 Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/MyNameIsShekhar/WAIFU-HUSBANDO-CATCHER
cd WAIFU-HUSBANDO-CATCHER

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Open .env and fill in all required values

# 4. Run
python -m waifu
```

---

## ⚙️ Configuration (`.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ | — | From [@BotFather](https://t.me/BotFather) |
| `OWNER_ID` | ✅ | — | Your Telegram user ID (integer) |
| `SUDO_IDS` | ➖ | — | Comma-separated extra sudo user IDs |
| `GROUP_ID` | ✅ | — | Log group for new-user notifications |
| `CHARA_CHANNEL_ID` | ✅ | — | Channel where character cards are posted |
| `MONGO_URI` | ✅ | — | MongoDB connection string |
| `DB_NAME` | ➖ | `waifu_bot` | Database name |
| `BOT_USERNAME` | ✅ | — | Without the `@` |
| `SUPPORT_CHAT` | ➖ | — | Support group username |
| `UPDATE_CHAT` | ➖ | — | Updates channel username |
| `PHOTO_URLS` | ➖ | — | Comma-separated fallback photo URLs |
| `DROP_INTERVAL_MINUTES` | ➖ | `15` | Timed auto-drop interval |
| `DEFAULT_MSG_FREQUENCY` | ➖ | `100` | Messages before a threshold drop |

---

## 🎴 Character Upload Methods

### Method 1 — Command line
```
/upload https://image.url Character-Name Anime-Name 3
```

| Rarity Number | Label |
|:---:|---|
| 1 | ⚪ Common |
| 2 | 🟣 Rare |
| 3 | 🟡 Legendary |
| 4 | 🟢 Medium |
| 5 | 💮 Special Edition |

---

### Method 2 — Reply to formatted post ✨

Post an image in your channel with this caption format:

```
🍀 Name: Sasha Braus
🍋 Rarity: Legendary
🌸 Anime: Attack On Titan
🌱 ID: 26
```

> The `ID` field is **optional** — auto-generated if omitted.

Then have a sudo user reply to that post with `/uploadchar`.
The bot parses the caption, reposts to the character channel, and inserts into the database automatically.

---

## 📦 Project Structure

```
waifu-catcher/
│
├── 📄 requirements.txt
├── 📄 .env.example
├── 📄 README.md
│
└── 📁 waifu/
    ├── 🐍 __init__.py       ← Shared singletons (db, application, collections)
    ├── 🐍 __main__.py       ← Entry point — run with `python -m waifu`
    ├── 🐍 config.py         ← All config loaded from .env
    │
    └── 📁 modules/
        ├── 🎲 waifu_drop.py  ← Core game loop (drops + /guess + anti-spam)
        ├── 🚀 start.py       ← /start, help panel
        ├── 📚 harem.py       ← Paginated collection view
        ├── 👤 profile.py     ← User stats, XP, rarity breakdown
        ├── 💰 economy.py     ← Coins, daily reward, marketplace
        ├── ⚔️  duel.py        ← PvP duel system
        ├── 🔄 trade.py       ← Character trading & gifting
        ├── 📤 upload.py      ← Two upload methods + /delete /update
        ├── 🏆 leaderboard.py
        ├── 🔍 inlinequery.py
        ├── 📢 broadcast.py
        ├── ⚙️  changetime.py
        ├── 🏓 ping.py
        └── 🛠️  eval.py
```

---

## 🗄️ MongoDB Collections

| Collection | Purpose |
|---|---|
| `anime_characters` | Master character catalogue |
| `users` | User docs — characters, coins, XP, favourites |
| `chat_settings` | Per-group drop frequency overrides |
| `group_user_totals` | Per-group per-user guess counts |
| `top_groups` | Global group activity for leaderboard |
| `pm_users` | Users who started the bot in PM |
| `market_listings` | Active marketplace listings |

---

## 💰 Economy System

| Action | Reward |
|---|---|
| 🎁 Daily claim | **200 coins** |
| ⚔️ Duel win | **150 coins + 100 XP** |
| ⚔️ Duel loss | **30 coins + 25 XP** |
| 🎯 Correct guess | **+50 XP** |

> **Marketplace** uses an escrow system — the character is removed from the seller's harem when listed and returned automatically on delist.

---

## 🔐 Security

- 🔒 All secrets stored in `.env` — never commit `.env` to git
- ✅ `sudo_users` is always a `set[int]` — no string-vs-int comparison bugs
- 🛡️ Anti-spam: 10 consecutive messages from same user → 10-minute ignore window
- ⏳ Trade / gift / duel: 5-minute expiry on pending actions via `asyncio.create_task`
- 🔑 `insert_one` replaced with `upsert` everywhere — no duplicate key errors

---

## 👥 Credits

<table>
  <tr>
    <td align="center">
      <b>Original Concept & Base Bot</b><br/>
      <a href="https://github.com/MyNameIsShekhar">@MyNameIsShekhar</a>
    </td>
    <td align="center">
      <b>Eval Module</b><br/>
      <a href="https://t.me/ishikki_Akabane">@ishikki_Akabane</a>
    </td>
    <td align="center">
      <b>Modified</b><br/>
      <a href="https://t.me/ITSANIMEN">彡 ΔNI_OTΔKU 彡</a>
  </tr>
</table>

> Built with ❤️ using [python-telegram-bot](https://python-telegram-bot.org), [Motor](https://motor.readthedocs.io), and [APScheduler](https://apscheduler.readthedocs.io).

---

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you'd like to change.

1. Fork the repo
2. Create your feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

<div align="center">

⭐ **Star this repo if it helped you!** ⭐

[![Telegram](https://img.shields.io/badge/Join%20Support-Telegram-26A5E4?style=for-the-badge&logo=telegram)](https://t.me/BotifyX_support)

</div>