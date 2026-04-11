import re
import time
from html import escape
from pymongo import ASCENDING
from telegram import InlineQueryResultCachedPhoto, InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.ext import CallbackContext, InlineQueryHandler
from waifu import application, collection, db, user_collection, LOGGER

_PAGE = 50


async def create_indexes() -> None:
    await db.anime_characters.create_index([("id",    ASCENDING)])
    await db.anime_characters.create_index([("anime", ASCENDING)])
    await db.users.create_index([("characters.id", ASCENDING)])


async def _batch_global(ids: list) -> dict:
    pipeline = [
        {"$unwind": "$characters"},
        {"$match":  {"characters.id": {"$in": ids}}},
        {"$group":  {"_id": "$characters.id", "n": {"$sum": 1}}},
    ]
    return {d["_id"]: d["n"] async for d in user_collection.aggregate(pipeline)}


async def _batch_anime(animes: list) -> dict:
    pipeline = [
        {"$match": {"anime": {"$in": animes}}},
        {"$group": {"_id": "$anime", "n": {"$sum": 1}}},
    ]
    return {d["_id"]: d["n"] async for d in collection.aggregate(pipeline)}


def _is_tg_file_id(s: str) -> bool:
    """True if looks like a Telegram file_id (not a URL)."""
    return bool(s) and not s.startswith("http")


# ── Main handler ──────────────────────────────────────────────────────────────

async def inlinequery(update: Update, context: CallbackContext) -> None:
    raw    = update.inline_query.query.strip()
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0
    user: dict | None = None
    chars: list[dict] = []

    _COLL_PREFIXES = ("collection.", "harem.")
    _is_user_query = any(raw.startswith(p) for p in _COLL_PREFIXES)

    if _is_user_query:
        # ── User's personal harem ────────────────────────────────────────────
        parts    = raw.split(" ", 1)
        uid_part = parts[0].split(".", 1)[1]
        search   = parts[1].strip() if len(parts) > 1 else ""

        if uid_part.isdigit():
            uid = int(uid_part)

            # Always fetch fresh — avoids stale data after new catches
            user = await user_collection.find_one({"id": uid})

            if user:
                deduped = list({c["id"]: c for c in user.get("characters", [])}.values())
                if search:
                    pat     = re.compile(re.escape(search), re.IGNORECASE)
                    deduped = [
                        c for c in deduped
                        if pat.search(c.get("name", "")) or pat.search(c.get("anime", ""))
                    ]

                # ── Refresh img_url from main collection (gets latest file_ids) ──
                if deduped:
                    ids_needed = [c["id"] for c in deduped]
                    fresh_docs = await collection.find(
                        {"id": {"$in": ids_needed}},
                        {"id": 1, "img_url": 1},
                    ).to_list(len(ids_needed))
                    fresh_map = {d["id"]: d.get("img_url", "") for d in fresh_docs}
                    for c in deduped:
                        fresh = fresh_map.get(c["id"], "")
                        if fresh:
                            c["img_url"] = fresh

                chars = deduped
                LOGGER.info(
                    "Inline harem uid=%s total=%d (after refresh) page_offset=%d",
                    uid, len(chars), offset,
                )

    else:
        # ── Global catalogue search ──────────────────────────────────────────
        if raw:
            query = {
                "$or": [
                    {"name":  {"$regex": re.escape(raw), "$options": "i"}},
                    {"anime": {"$regex": re.escape(raw), "$options": "i"}},
                ]
            }
            chars = await collection.find(query).to_list(5000)
        else:
            chars = await collection.find({}).to_list(5000)

    # ── Paginate ──────────────────────────────────────────────────────────────
    page_chars  = chars[offset:offset + _PAGE]
    next_offset = str(offset + len(page_chars)) if len(page_chars) == _PAGE else ""

    if not page_chars:
        # Show helpful "no results" card for harem queries
        if _is_user_query:
            no_res = [InlineQueryResultArticle(
                id="no_chars",
                title="📭 No characters with images yet",
                description="Catch more characters or run /migrateimgs to fix images",
                input_message_content=InputTextMessageContent(
                    "📭 No characters with valid images found in this harem yet.\n"
                    "Catch more characters in the group!"
                ),
            )]
            await update.inline_query.answer(no_res, cache_time=5)
        else:
            await update.inline_query.answer([], cache_time=5)
        return

    # ── Batch DB stats ────────────────────────────────────────────────────────
    ids     = [c["id"]    for c in page_chars]
    animes  = list({c["anime"] for c in page_chars})
    g_count = await _batch_global(ids)
    a_total = await _batch_anime(animes)

    # ── Build results — only file_ids work reliably in inline mode ────────────
    results       = []
    skipped_no_fid = 0

    for c in page_chars:
        name    = escape(c.get("name",  "Unknown"))
        anime   = escape(c.get("anime", "Unknown"))
        img_raw = c.get("img_url", "")

        if user and _is_user_query:
            u_cnt = sum(1 for x in user.get("characters", []) if x["id"] == c["id"])
            u_an  = sum(1 for x in user.get("characters", []) if x["anime"] == c["anime"])
            db_an = a_total.get(c["anime"], "?")
            uid_v = user.get("id", "")
            uname = escape(user.get("first_name", str(uid_v)))
            cap   = (
                f"<b><a href='tg://user?id={uid_v}'>{uname}</a>'s Character</b>\n\n"
                f"🌸 <b>{name}</b> ×{u_cnt}\n"
                f"📺 <b>{anime}</b> ({u_an}/{db_an})\n"
                f"💎 {c.get('rarity', '')}\n"
                f"🆔 {c['id']}"
            )
        else:
            gc  = g_count.get(c["id"], 0)
            cap = (
                f"🌸 <b>{name}</b>\n\n"
                f"📺 {anime}\n"
                f"💎 {c.get('rarity', '')}\n"
                f"🆔 {c['id']}\n\n"
                f"Guessed globally <b>{gc}</b> time{'s' if gc != 1 else ''}."
            )

        result_id = f"{c['id']}_{time.time_ns()}"

        if _is_tg_file_id(img_raw):
            # Valid Telegram file_id → CachedPhoto (best quality, always works)
            results.append(InlineQueryResultCachedPhoto(
                id=result_id,
                photo_file_id=img_raw,
                caption=cap,
                parse_mode="HTML",
            ))
        else:
            # Expired CDN URL or missing — skip; user should run /migrateimgs
            skipped_no_fid += 1

    if skipped_no_fid:
        LOGGER.info(
            "Inline: skipped %d chars with no valid file_id (run /migrateimgs to fix)",
            skipped_no_fid,
        )

    if not results:
        no_res = [InlineQueryResultArticle(
            id="no_img",
            title="⚠️ Images not migrated yet",
            description="Owner: run /migrateimgs in bot PM to fix character images",
            input_message_content=InputTextMessageContent(
                "⚠️ Character images need migration.\n"
                "Owner should run /migrateimgs in bot PM."
            ),
        )]
        await update.inline_query.answer(no_res, cache_time=5)
        return

    try:
        await update.inline_query.answer(results, next_offset=next_offset, cache_time=5)
        LOGGER.info("Inline: answered %d results", len(results))
    except Exception as e:
        LOGGER.error("Inline query answer failed: %s", e)


application.add_handler(InlineQueryHandler(inlinequery, block=False))
