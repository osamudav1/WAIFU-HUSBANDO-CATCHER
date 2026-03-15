import asyncio, io, textwrap, traceback
from contextlib import redirect_stdout
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes
from waifu import application, DEV_LIST

_ns: dict[int, dict] = {}
_MAX = 3800


def _ns_get(chat_id: int, update: Update, bot) -> dict:
    if chat_id not in _ns:
        _ns[chat_id] = {"__builtins__": globals()["__builtins__"], "bot": bot,
                        "update": update, "effective_user": update.effective_user,
                        "effective_chat": update.effective_chat,
                        "effective_message": update.effective_message}
    else:
        _ns[chat_id].update({"update": update, "bot": bot,
                              "effective_user": update.effective_user,
                              "effective_chat": update.effective_chat,
                              "effective_message": update.effective_message})
    return _ns[chat_id]


def _clean(code: str) -> str:
    if code.startswith("```") and code.endswith("```"):
        return "\n".join(code.split("\n")[1:-1])
    return code.strip("` \n")


async def _send(text: str | None, update: Update, bot) -> None:
    if not text:
        return
    text = str(text).strip()
    tid  = update.effective_message.message_thread_id if update.effective_chat.is_forum else None
    cid  = update.effective_chat.id
    if len(text) > _MAX:
        buf = io.BytesIO(text.encode()); buf.name = "output.txt"
        await bot.send_document(cid, buf, message_thread_id=tid)
    else:
        esc = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        await bot.send_message(cid, f"<pre>{esc}</pre>", parse_mode=ParseMode.HTML, message_thread_id=tid)


async def _run(kind, update: Update, bot) -> str | None:
    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        return "No code."
    body = _clean(parts[1])
    env  = _ns_get(update.message.chat_id, update, bot)
    out  = io.StringIO()
    if kind is eval:
        try:
            with redirect_stdout(out):
                r = eval(body, env)  # noqa
            return f"{out.getvalue()}{repr(r)}" if r is not None else out.getvalue() or None
        except Exception:
            return traceback.format_exc()
    to_compile = f"async def _f():\n{textwrap.indent(body,'  ')}"
    try:
        exec(to_compile, env)  # noqa
    except Exception:
        return traceback.format_exc()
    try:
        with redirect_stdout(out):
            ret = await env["_f"]()
    except Exception:
        return f"{out.getvalue()}{traceback.format_exc()}"
    return f"{out.getvalue()}{ret}" if ret is not None else (out.getvalue() or None)


def _dev(fn):
    async def w(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in DEV_LIST:
            return
        return await fn(update, context)
    w.__name__ = fn.__name__
    return w


@_dev
async def evaluate(update, context):
    await _send(await _run(eval, update, context.bot), update, context.bot)

@_dev
async def execute(update, context):
    await _send(await _run(exec, update, context.bot), update, context.bot)

@_dev
async def shell(update, context):
    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        await _send("No command.", update, context.bot); return
    proc = await asyncio.create_subprocess_shell(
        parts[1], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    await _send(stdout.decode(errors="replace"), update, context.bot)

@_dev
async def clear(update, context):
    cid = update.message.chat_id
    if cid in _ns:
        del _ns[cid]
        msg = f"✅ Cleared locals for <code>{cid}</code>."
    else:
        msg = f"ℹ️ No locals for <code>{cid}</code>."
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler(("e","ev","eva","eval"), evaluate, block=False))
application.add_handler(CommandHandler(("x","ex","exe","exec","py"), execute, block=False))
application.add_handler(CommandHandler("sh", shell, block=False))
application.add_handler(CommandHandler("clearlocals", clear, block=False))
