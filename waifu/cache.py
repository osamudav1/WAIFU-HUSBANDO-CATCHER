"""
waifu/cache.py — Cache System + Message Queue + DB Rate Limiter

1. Cache System   : TTLCache for users & character list  (DB round-trips လျော့)
2. Message Queue  : Per-user asyncio.Lock               (spam/concurrent crashes ကာကွယ်)
3. DB Semaphore   : Global asyncio.Semaphore            (MongoDB overload ကာကွယ်)
"""
import asyncio
import time
from collections import defaultdict
from typing import Any

from cachetools import TTLCache

# ── 1. Cache Stores ────────────────────────────────────────────────────────────

# User document cache  — 1 000 users, 30-second TTL
_user_cache: TTLCache = TTLCache(maxsize=1_000, ttl=30)

# Full character list cache — drop တိုင်း DB မဆင်းရအောင် — 2 minute TTL
_char_list_cache: TTLCache = TTLCache(maxsize=10, ttl=120)

# Lightweight chat-settings cache (drop threshold per group) — 5 minute TTL
_chat_cfg_cache: TTLCache = TTLCache(maxsize=500, ttl=300)

# ── 2. DB Semaphore (max concurrent MongoDB ops) ──────────────────────────────
# 20 groups simultaneously asking for drops → still safe
_db_sem = asyncio.Semaphore(20)


class _DbSemCtx:
    """Async context manager for the DB semaphore."""
    __slots__ = ()

    async def __aenter__(self):
        await _db_sem.acquire()

    async def __aexit__(self, *_):
        _db_sem.release()


def db_op() -> _DbSemCtx:
    """
    Use around any MongoDB call to prevent overload:

        async with db_op():
            doc = await user_collection.find_one(...)
    """
    return _DbSemCtx()


# ── 3. Per-user Message Queue (Lock) ──────────────────────────────────────────
# Keeps one concurrent operation per user → crash / race-condition free
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_lock_last_used: dict[int, float] = {}
_LOCK_TTL = 300  # clean up unused locks after 5 minutes


def user_lock(uid: int) -> asyncio.Lock:
    """
    Return the asyncio.Lock for this user.  Use as:

        async with user_lock(uid):
            ...  # only ONE coroutine per user runs at a time
    """
    _lock_last_used[uid] = time.monotonic()
    return _user_locks[uid]


def cleanup_locks() -> int:
    """Remove locks unused for > _LOCK_TTL seconds.  Call periodically."""
    now = time.monotonic()
    stale = [uid for uid, t in _lock_last_used.items() if now - t > _LOCK_TTL]
    for uid in stale:
        _user_locks.pop(uid, None)
        _lock_last_used.pop(uid, None)
    return len(stale)


# ── User cache helpers ─────────────────────────────────────────────────────────

def get_user(uid: int) -> dict | None:
    return _user_cache.get(uid)


def set_user(uid: int, doc: dict) -> None:
    if doc:
        _user_cache[uid] = doc


def invalidate_user(uid: int) -> None:
    _user_cache.pop(uid, None)


# ── Character list cache helpers ──────────────────────────────────────────────

_CHAR_LIST_KEY = "all"


def get_char_list() -> list[dict] | None:
    return _char_list_cache.get(_CHAR_LIST_KEY)


def set_char_list(chars: list[dict]) -> None:
    _char_list_cache[_CHAR_LIST_KEY] = chars


def invalidate_char_list() -> None:
    """Call after upload/delete/update of any character."""
    _char_list_cache.pop(_CHAR_LIST_KEY, None)


# ── Chat config cache helpers ─────────────────────────────────────────────────

def get_chat_cfg(chat_id: int) -> int | None:
    return _chat_cfg_cache.get(chat_id)


def set_chat_cfg(chat_id: int, threshold: int) -> None:
    _chat_cfg_cache[chat_id] = threshold


def invalidate_chat_cfg(chat_id: int) -> None:
    _chat_cfg_cache.pop(chat_id, None)


# ── Cache stats (for /ping or /stats) ─────────────────────────────────────────

def cache_stats() -> dict[str, Any]:
    return {
        "user_cache":      {"size": len(_user_cache),      "maxsize": _user_cache.maxsize,      "ttl": _user_cache.ttl},
        "char_list_cache": {"size": len(_char_list_cache), "maxsize": _char_list_cache.maxsize, "ttl": _char_list_cache.ttl},
        "chat_cfg_cache":  {"size": len(_chat_cfg_cache),  "maxsize": _chat_cfg_cache.maxsize,  "ttl": _chat_cfg_cache.ttl},
        "active_user_locks": len(_user_locks),
        "db_sem_available":  _db_sem._value,
    }
