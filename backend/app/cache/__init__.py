"""Cache construction.

One entry point, so no call site has to know whether Redis was configured.
"""

from __future__ import annotations

import logging

from app.cache.backends import FallbackCache, PostgresCache, RedisCache
from app.cache.base import Cache, NullCache, http_cache_key
from app.config import settings
from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

_cache: Cache | None = None


def build_cache() -> Cache:
    """Postgres alone when no Redis URL is set; Redis in front of Postgres when
    one is. The deployed demo runs the first path deliberately — see the
    deploy-scope decision in CLAUDE.md."""
    postgres = PostgresCache(get_sessionmaker())
    if not settings.redis_url:
        logger.info("No REDIS_URL configured; using the Postgres-backed cache.")
        return postgres
    return FallbackCache(primary=RedisCache(settings.redis_url), standby=postgres)


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
