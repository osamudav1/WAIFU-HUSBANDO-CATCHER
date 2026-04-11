"""
waifu/__main__.py  —  Entry point.
Run with:  python -m waifu

Mode selection (automatic):
  - Deployed on Replit  → REPLIT_DEPLOYMENT=1 is set → webhook mode (no Conflict)
  - Dev / local         → polling mode
"""
import importlib
import os

from waifu import ALL_MODULES, LOGGER


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

    # ── Startup notification to ALL known groups ─────────────────────────────
    from waifu import (
        top_global_groups_collection,
        group_user_totals_collection,
        user_totals_collection,
    )
    import asyncio as _asyncio

    startup_msg = (
        "╔══════════════════════╗\n"
        "║  🌸  <b>ᴡᴀɪꜰᴜ ʙᴏᴛ ᴏɴʟɪɴᴇ</b>  🌸  ║\n"
        "╚══════════════════════╝\n\n"
        "⚡ <b>ꜱʏꜱᴛᴇᴍ ʙᴏᴏᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ</b>\n"
        "🎴 ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅʀᴏᴘꜱ ᴀʀᴇ ɴᴏᴡ ᴀᴄᴛɪᴠᴇ\n"
        "🏆 ᴄᴏᴍᴘᴇᴛᴇ • ᴄᴏʟʟᴇᴄᴛ • ᴄᴏɴQᴜᴇʀ\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    g1 = set(await top_global_groups_collection.distinct("group_id"))
    g2 = set(await group_user_totals_collection.distinct("group_id"))
    g3 = set(await user_totals_collection.distinct("chat_id"))
    all_groups = list(g1 | g2 | g3)
    # Always include main GROUP_ID
    if GROUP_ID and GROUP_ID not in all_groups:
        all_groups.append(GROUP_ID)

    ok = fail = 0
    for gid in all_groups:
        try:
            await application.bot.send_message(gid, startup_msg, parse_mode="HTML")
            ok += 1
        except Exception as e:
            err = str(e)
            if "New chat id:" in err:
                import re as _re
                m = _re.search(r"New chat id:\s*(-?\d+)", err)
                if m:
                    new_id = int(m.group(1))
                    try:
                        await application.bot.send_message(new_id, startup_msg, parse_mode="HTML")
                        ok += 1
                        continue
                    except Exception:
                        pass
            fail += 1
        await _asyncio.sleep(0.05)

    LOGGER.info("Startup message sent: %d ok, %d failed (total %d groups)", ok, fail, len(all_groups))


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
        # Webhook mode — deployed Replit VM; Telegram pushes updates to us.
        # No polling conflict with the dev environment.
        port = int(os.environ.get("PORT", "8080"))
        domains = os.environ.get("REPLIT_DOMAINS", "")
        domain = domains.split(",")[0].strip() if domains else ""

        if not domain:
            LOGGER.error("REPLIT_DOMAINS is empty; cannot start webhook. Falling back to polling.")
            LOGGER.info("Starting bot (polling fallback)…")
            application.run_polling(drop_pending_updates=True)
            return

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
