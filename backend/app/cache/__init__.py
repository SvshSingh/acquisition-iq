"""Cache construction.

One entry point, so no call site has to know whether Redis was configured.
"""

from __future__ import annotations

import logging

from app.cache.backends import FallbackCache, PostgresCache, RedisCache
from app.cache.base import Cache, NullCache, http_cache_key
from app.config import settings
from app.db.session import database_configured, get_sessionmaker

logger = logging.getLogger(__name__)

_cache: Cache | None = None


def build_cache() -> Cache:
    """Redis in front of Postgres in front of nothing.

    Every layer is guarded, including the last one. The original version guarded
    only Redis and handed back a bare `PostgresCache`, on the assumption that if
    the app is up its own database is up. That is false for this deployment:
    the API serves a committed snapshot and Render provisions no database, so a
    live refresh — the one request that actually touches the cache — raised
    `ConnectionRefusedError` instead of simply running uncached.

    A cache is an optimisation, and an optimisation that can fail a request is a
    liability. That principle was already written down here; it just was not
    applied all the way to the bottom of the stack. Now the final standby is a
    cache that always misses and never raises, so the worst outcome of having no
    storage at all is doing the work twice.
    """
    durable: Cache
    if database_configured():
        durable = FallbackCache(primary=PostgresCache(get_sessionmaker()), standby=NullCache())
    else:
        # No database is a configuration, not a fault, so there is nothing to
        # fall back *from*: go straight to the cache that always misses rather
        # than attempting a connection that cannot exist on every lookup.
        durable = NullCache()
    if not settings.redis_url:
        logger.info(
            "No REDIS_URL configured; using the %s cache.",
            "Postgres-backed" if database_configured() else "null (uncached)",
        )
        return durable
    return FallbackCache(primary=RedisCache(settings.redis_url), standby=durable)


def get_cache() -> Cache:
    """Process-wide cache, built on first use."""
    global _cache
    if _cache is None:
        _cache = build_cache()
    return _cache


async def close_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.close()
        _cache = None


__all__ = [
    "Cache",
    "FallbackCache",
    "NullCache",
    "PostgresCache",
    "RedisCache",
    "build_cache",
    "close_cache",
    "get_cache",
    "http_cache_key",
]
