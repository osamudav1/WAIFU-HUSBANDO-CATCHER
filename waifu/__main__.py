"""
waifu/__main__.py  —  Entry point.
Run with:  python -m waifu

Mode selection (automatic):
  - Fly.io          → FLY_APP_NAME is set    → polling + health server on PORT
  - Koyeb           → KOYEB_APP_NAME is set  → polling + health server on PORT
  - Hugging Face    → SPACE_ID is set        → polling + health server on 7860
  - Render          → RENDER=true            → polling (worker; no health server)
  - Replit VM       → REPLIT_DEPLOYMENT=1    → webhook mode (no Conflict)
  - Dev / local     → polling mode (no health server)
"""
import importlib
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from waifu import ALL_MODULES, LOGGER


def _run_health_server(port: int = 8080) -> None:
    """Minimal HTTP health-check server — required by Koyeb / Replit deployments."""
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass

    server = HTTPServer(("0.0.0.0", port), _Handler)
    LOGGER.info("Health-check server listening on port %d", port)
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
    await _migrate_indexes()
    await create_indexes()
    LOGGER.info("DB indexes ensured.")
    # Always clear any leftover webhook so polling gets all updates
    try:
        await application.bot.delete_webhook(drop_pending_updates=False)
        LOGGER.info("Webhook cleared — polling will receive all updates.")
    except Exception as e:
        LOGGER.warning("Could not clear webhook: %s", e)


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

    _ALLOWED_UPDATES = [
        "message", "edited_message", "callback_query",
        "inline_query", "chosen_inline_result",
        "chat_member", "my_chat_member",
        "pre_checkout_query", "shipping_query",
    ]
    _POLLING_KWARGS = dict(
        drop_pending_updates=True,
        allowed_updates=_ALLOWED_UPDATES,
    )

    # ── Detect platform ────────────────────────────────────────────────────────
    is_fly      = bool(os.environ.get("FLY_APP_NAME"))
    is_koyeb    = bool(os.environ.get("KOYEB_APP_NAME"))
    is_hf       = bool(os.environ.get("SPACE_ID"))
    is_render   = os.environ.get("RENDER", "").lower() == "true"
    is_deployed = os.environ.get("REPLIT_DEPLOYMENT", "0") == "1"

    if is_fly:
        # ── Fly.io: health server + polling ───────────────────────────────────
        port = int(os.environ.get("PORT", "8080"))
        t = threading.Thread(target=_run_health_server, args=(port,), daemon=True)
        t.start()
        LOGGER.info("Fly.io mode: polling + health server on port %d", port)
        application.run_polling(**_POLLING_KWARGS)

    elif is_koyeb:
        # ── Koyeb: health server + polling ────────────────────────────────────
        port = int(os.environ.get("PORT", "8080"))
        t = threading.Thread(target=_run_health_server, args=(port,), daemon=True)
        t.start()
        LOGGER.info("Koyeb mode: polling + health server on port %d", port)
        application.run_polling(**_POLLING_KWARGS)

    elif is_hf:
        # ── Hugging Face Spaces: health server on 7860 + polling ──────────────
        port = int(os.environ.get("PORT", "7860"))
        t = threading.Thread(target=_run_health_server, args=(port,), daemon=True)
        t.start()
        LOGGER.info("Hugging Face Spaces mode: polling + health server on port %d", port)
        application.run_polling(**_POLLING_KWARGS, bootstrap_retries=-1)

    elif is_render:
        # ── Render worker: plain polling (no HTTP port needed for workers) ─────
        LOGGER.info("Render mode: polling…")
        application.run_polling(**_POLLING_KWARGS)

    elif is_deployed:
        # ── Replit VM deployment ───────────────────────────────────────────────
        port    = int(os.environ.get("PORT", "8080"))
        domains = os.environ.get("REPLIT_DOMAINS", "")
        domain  = domains.split(",")[0].strip() if domains else ""
        token   = os.environ.get("BOT_TOKEN", "")

        if domain and token:
            url_path    = token
            webhook_url = f"https://{domain}/{url_path}"
            LOGGER.info("Replit webhook mode: port=%d url=https://%s/...", port, domain)
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=url_path,
                webhook_url=webhook_url,
                drop_pending_updates=True,
                allowed_updates=_ALLOWED_UPDATES,
            )
        else:
            LOGGER.warning("REPLIT_DOMAINS/TOKEN empty — falling back to polling + health server on port %d", port)
            t = threading.Thread(target=_run_health_server, args=(port,), daemon=True)
            t.start()
            application.run_polling(**_POLLING_KWARGS)

    else:
        # ── Local dev: simple polling ─────────────────────────────────────────
        LOGGER.info("Starting bot (polling)…")
        application.run_polling(**_POLLING_KWARGS)


if __name__ == "__main__":
    main()
