"""
waifu/__main__.py  —  Entry point.
Run with:  python -m waifu
"""
import importlib

from waifu import ALL_MODULES, LOGGER


async def _migrate_indexes() -> None:
    """
    Drop legacy indexes from the old bot schema that conflict with new code.
    Safe to call on every startup — silently skips if they don't exist.

    The old bot had a unique index on `user_id` in the users collection.
    New code uses `id` as the user identifier, so every new insert had
    user_id=null, causing DuplicateKeyError for any second user.
    """
    from waifu import user_collection
    try:
        await user_collection.drop_index("user_id_1")
        LOGGER.info("Migration: dropped stale index users.user_id_1")
    except Exception:
        pass   # index didn't exist — nothing to do


async def _post_init(application) -> None:
    """Runs once after the Application starts — migrations, indexes, scheduler."""
    from waifu.modules.inlinequery import create_indexes
    from waifu.modules.waifu_drop  import start_scheduler
    await _migrate_indexes()
    await create_indexes()
    LOGGER.info("DB indexes ensured.")
    start_scheduler(application.bot)
    LOGGER.info("Drop scheduler started.")


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

    LOGGER.info("Starting bot (polling)…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()