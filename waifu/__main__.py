"""
waifu/__main__.py  —  Entry point.
Run with:  python -m waifu

Mode selection (automatic):
  - Deployed on Replit  → REPLIT_DEPLOYMENT=1 is set → webhook mode (no Conflict)
  - Dev / local         → polling mode
"""
import importlib
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from waifu import ALL_MODULES, LOGGER


def _run_health_server(port: int = 8080) -> None:
    """Tiny HTTP server for Replit health checks — keeps deployment alive."""
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass   # silence access logs

    server = HTTPServer(("0.0.0.0", port), _Handler)
    server.serve_forever()


async def _migrate_indexes() -> None:
    from waifu import user_collection
    try:
        await user_collection.drop_index("user_id_1")
        LOGGER.info("Migration: dropped stale index users.user_id_1")
    except Exception:
        pass


async def _post_init(application) -> None:
    from waifu.modules.inlinequery import create_indexes
    from waifu import GROUP_ID
    await _migrate_indexes()
    await create_indexes()
    LOGGER.info("DB indexes ensured.")


def main() -> None:
    LOGGER.info("Loading %d module(s)…", len(ALL_MODULES))
    for name in ALL_MODULES:
        try:
            importlib.import_module(f"waifu.modules.{name}")
            LOGGER.debug("  ✓ %s", name)
        except Exception as exc:
            LOGGER.error("  ✗ %s — %s", name, exc, exc_info=True)
            raise
    LOGGER.info("All modules loaded.")

    from waifu import application
    application.post_init = _post_init

    # ── Auto-detect mode ──────────────────────────────────────────────────────
    is_deployed = os.environ.get("REPLIT_DEPLOYMENT", "0") == "1"

    if is_deployed:
        port    = int(os.environ.get("PORT", "8080"))
        domains = os.environ.get("REPLIT_DOMAINS", "")
        domain  = domains.split(",")[0].strip() if domains else ""

        if domain:
            # Webhook mode — Telegram pushes updates; HTTP server built-in.
            webhook_url = f"https://{domain}/{os.environ.get('BOT_TOKEN', '')}"
            LOGGER.info("Starting bot (webhook) on port %d → %s", port, f"https://{domain}/...")
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                webhook_url=webhook_url,
                drop_pending_updates=True,
                allowed_updates=[
                    "message", "edited_message", "callback_query",
                    "inline_query", "chosen_inline_result",
                    "chat_member", "my_chat_member",
                ],
            )
        else:
            # No domain → polling + health-check HTTP server (keeps deployment alive).
            LOGGER.warning("REPLIT_DOMAINS empty — polling + health server on port %d", port)
            t = threading.Thread(target=_run_health_server, args=(port,), daemon=True)
            t.start()
            application.run_polling(
                drop_pending_updates=True,
                allowed_updates=[
                    "message", "edited_message", "callback_query",
                    "inline_query", "chosen_inline_result",
                    "chat_member", "my_chat_member",
                ],
            )
    else:
        LOGGER.info("Starting bot (polling)…")
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=[
                "message", "edited_message", "callback_query",
                "inline_query", "chosen_inline_result",
                "chat_member", "my_chat_member",
            ],
        )


if __name__ == "__main__":
    main()
