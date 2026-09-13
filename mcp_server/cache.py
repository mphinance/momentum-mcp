"""
cache.py — Market-hours-aware TTL cache with request coalescing.

Provides a decorator that caches async function results with TTL that
adapts based on US market status:
  - Market open (9:30-16:00 ET weekdays): configurable TTL (default 5 min)
  - Market closed (extended hours weekdays): configurable TTL (default 60 min)
  - Weekends & market holidays: 24 hour TTL (data won't change)

Request coalescing ("singleflight"): when multiple concurrent callers
request the same key, only ONE upstream call is made.  All others await
the same asyncio.Future.  This prevents the "thundering herd" problem
where 10 users asking for AAPL technicals simultaneously trigger 10
redundant yfinance downloads.

Usage:
    from mcp_server.cache import smart_cache

    @smart_cache(open_ttl=300, closed_ttl=3600)
    async def get_historical_data(ticker, period="3mo", interval="1d"):
        ...
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ── US Market Schedule ────────────────────────────────────────────────────────

ET = ZoneInfo("America/New_York")

# 2025-2026 NYSE holidays (dates the market is CLOSED)
_MARKET_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
}


def is_market_open() -> bool:
    """Check if US stock market is currently open."""
    now = datetime.now(ET)

    # Weekend
    if now.weekday() >= 5:
        return False

    # Holiday
    if now.strftime("%Y-%m-%d") in _MARKET_HOLIDAYS:
        return False

    # Regular trading hours: 9:30 AM - 4:00 PM ET
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= now <= market_close


def get_market_status() -> str:
    """Return human-readable market status."""
    now = datetime.now(ET)

    if now.weekday() >= 5:
        return "weekend"
    if now.strftime("%Y-%m-%d") in _MARKET_HOLIDAYS:
        return "holiday"
    if is_market_open():
        return "open"
    return "closed"


def get_ttl(open_ttl: int = 300, closed_ttl: int = 3600) -> int:
    """Return appropriate TTL in seconds based on market status."""
    status = get_market_status()
    if status == "open":
        return open_ttl
    elif status in ("weekend", "holiday"):
        return 86400  # 24 hours — data is frozen
    else:
        return closed_ttl


# ── Cache Storage ─────────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, Any]] = {}  # key -> (expires_at, value)
_cache_hits = 0
_cache_misses = 0

# ── Request Coalescing (Singleflight) ─────────────────────────────────────────
# When multiple callers request the same cache key simultaneously,
# only ONE upstream call is made.  Others await the same Future.
_inflight: dict[str, asyncio.Future] = {}  # key -> Future (while a request is in-flight)


def _make_key(func_name: str, args: tuple, kwargs: dict) -> str:
    """Create a deterministic cache key from function name + arguments."""
    # Serialize args/kwargs to a stable string
    parts = [func_name]
    for a in args:
        parts.append(str(a))
    for k in sorted(kwargs.keys()):
        parts.append(f"{k}={kwargs[k]}")
    raw = "|".join(parts)
    return hashlib.md5(raw.encode()).hexdigest()


def smart_cache(open_ttl: int = 300, closed_ttl: int = 3600):
    """Decorator: cache async function results with market-hours-aware TTL.

    Features:
    - Market-hours-aware TTL (open/closed/weekend)
    - Request coalescing: concurrent identical requests share one upstream call
    - Automatic expired-entry eviction

    Args:
        open_ttl: TTL in seconds during market hours (default: 5 min)
        closed_ttl: TTL in seconds when market is closed (default: 60 min)
                    Weekends/holidays automatically use 24h.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            global _cache_hits, _cache_misses

            key = _make_key(f"{func.__module__}.{func.__qualname__}", args, kwargs)
            now = time.time()

            # ── 1. Check cache ──
            if key in _cache:
                expires_at, value = _cache[key]
                if now < expires_at:
                    _cache_hits += 1
                    logger.debug("Cache HIT: %s (expires in %ds)", func.__name__, int(expires_at - now))
                    return value

            # ── 2. Check if another caller is already fetching this key ──
            if key in _inflight:
                _cache_hits += 1  # counts as a "hit" — no upstream call
                logger.debug("Coalesce HIT: %s (awaiting in-flight request)", func.__name__)
                return await _inflight[key]

            # ── 3. Cache miss — we are the leader, make the upstream call ──
            _cache_misses += 1
            ttl = get_ttl(open_ttl, closed_ttl)
            logger.info("Cache MISS: %s (TTL=%ds, market=%s)", func.__name__, ttl, get_market_status())

            # Create a Future that other concurrent callers can await
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            _inflight[key] = future

            try:
                result = await func(*args, **kwargs)

                # Store in cache
                _cache[key] = (now + ttl, result)

                # Resolve the future so all awaiters get the result
                if not future.done():
                    future.set_result(result)

                return result

            except Exception as exc:
                # Propagate the error to all awaiters
                if not future.done():
                    future.set_exception(exc)
                raise

            finally:
                # Always clean up the in-flight tracker
                _inflight.pop(key, None)

                # Evict expired entries periodically (every 100 misses)
                if _cache_misses % 100 == 0:
                    _evict_expired()

        return wrapper
    return decorator


def _evict_expired() -> int:
    """Remove expired cache entries. Returns count evicted."""
    now = time.time()
    expired = [k for k, (exp, _) in _cache.items() if now >= exp]
    for k in expired:
        del _cache[k]
    if expired:
        logger.info("Cache: evicted %d expired entries, %d remaining", len(expired), len(_cache))
    return len(expired)


def get_cache_stats() -> dict:
    """Return cache statistics."""
    now = time.time()
    active = sum(1 for _, (exp, _) in _cache.items() if now < exp)
    return {
        "total_entries": len(_cache),
        "active_entries": active,
        "expired_entries": len(_cache) - active,
        "hits": _cache_hits,
        "misses": _cache_misses,
        "hit_rate": round(_cache_hits / max(_cache_hits + _cache_misses, 1) * 100, 1),
        "inflight": len(_inflight),
        "market_status": get_market_status(),
        "current_ttl_seconds": get_ttl(),
    }


def clear_cache() -> int:
    """Clear all cache entries. Returns count cleared."""
    count = len(_cache)
    _cache.clear()
    logger.info("Cache: cleared %d entries", count)
    return count
