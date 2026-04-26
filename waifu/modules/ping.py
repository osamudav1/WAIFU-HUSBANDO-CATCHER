import time
import psutil
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, CallbackContext
from waifu import application, OWNER_ID, sudo_users, StartTime


def _uptime(s: float) -> str:
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _bar(pct: float, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


async def ping(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id
    if uid not in sudo_users and uid != OWNER_ID:
        await update.message.reply_text("❌ Sudo only.")
        return

    t0  = time.monotonic()
    msg = await update.message.reply_text("🏓 Pong!")
    ms  = round((time.monotonic() - t0) * 1000, 2)

    cpu   = psutil.cpu_percent(interval=0.3)
    ram   = psutil.virtual_memory()
    disk  = psutil.disk_usage("/")

    ram_used  = ram.used  / 1024 ** 3
    ram_total = ram.total / 1024 ** 3
    disk_used  = disk.used  / 1024 ** 3
    disk_total = disk.total / 1024 ** 3

    text = (
        f"🏓 <b>Pong!</b>  <code>{ms} ms</code>\n"
        f"⏱️ Uptime: <code>{_uptime(time.time() - StartTime)}</code>\n\n"
        f"<b>── System Stats ──</b>\n"
        f"🖥️ CPU:  {_bar(cpu)} <code>{cpu:.1f}%</code>\n"
        f"🧠 RAM:  {_bar(ram.percent)} <code>{ram_used:.1f} / {ram_total:.1f} GB  ({ram.percent:.1f}%)</code>\n"
        f"💾 Disk: {_bar(disk.percent)} <code>{disk_used:.1f} / {disk_total:.1f} GB  ({disk.percent:.1f}%)</code>"
    )
    await msg.edit_text(text, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("ping", ping, block=False))
