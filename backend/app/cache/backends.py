"""Cache backends: Redis, Postgres, and the fallback wrapper that ties them.

The Postgres backend is not a toy. It is the path the deployed demo actually
runs on (see the deploy-scope decision in CLAUDE.md), so it does real TTL
expiry and real upserts rather than pretending.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.base import Cache, NullCache
from app.db.models import HttpCacheEntry

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis/Upstash backend.

    Constructed from a URL rather than a client so the caller never has to import
    redis to use the cache.
    """

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis  # imported lazily: optional dependency in practice

        self._client = redis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)
        return str(value) if value is not None else None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def close(self) -> None:
        await self._client.aclose()


class PostgresCache:
    """Cache table in the primary database.

    Slower than Redis and honest about it. The win is that it has no separate
    availability story: if the app can serve a request at all, the cache is up.

    Expiry is enforced on read rather than by a background sweeper, so a stale
    row is never served even if nothing has swept recently. `purge_expired`
    exists to keep the table from growing without bound, not for correctness.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, key: str) -> str | None:
        async with self._sessionmaker() as session:
            row = await session.scalar(
                select(HttpCacheEntry).where(
                    HttpCacheEntry.key == key,
                    HttpCacheEntry.expires_at > datetime.now(UTC),
                )
            )
            return row.body if row else None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        now = datetime.now(UTC)
        stmt = (
            pg_insert(HttpCacheEntry)
            .values(
                key=key,
                url="",
                status_code=200,
                body=value,
                headers={},
                fetched_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            # A re-fetch of a key we already hold is a refresh, not a conflict.
            .on_conflict_do_update(
                index_elements=[HttpCacheEntry.key],
                set_={
                    "body": value,
                    "fetched_at": now,
                    "expires_at": now + timedelta(seconds=ttl_seconds),
                },
            )
        )
        async with self._sessionmaker() as session:
            await session.execute(stmt)
            await session.commit()

    async def delete(self, key: str) -> None:
        async with self._sessionmaker() as session:
            await session.execute(delete(HttpCacheEntry).where(HttpCacheEntry.key == key))
            await session.commit()

    async def purge_expired(self) -> int:
        """Housekeeping. Returns the number of rows removed."""
        async with self._sessionmaker() as session:
            # session.execute() is typed as returning Result, but a DELETE always
            # produces a CursorResult, which is the only one carrying rowcount.
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    delete(HttpCacheEntry).where(HttpCacheEntry.expires_at <= datetime.now(UTC))
                ),
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def close(self) -> None:
        return None


class FallbackCache:
    """Primary cache with a standby behind it.

    Every Redis call is wrapped: a cache is an optimisation, and an optimisation
    that can take down a request is a liability. On the first failure we log once
    and switch to the standby for the rest of the process's life rather than
    paying a timeout on every subsequent call.
    """

    def __init__(self, primary: Cache, standby: Cache) -> None:
        self._primary = primary
        self._standby = standby
        self._primary_failed = False

    @property
    def using_standby(self) -> bool:
        return self._primary_failed

    def _demote(self, exc: Exception) -> None:
        if not self._primary_failed:
            logger.warning(
                "Primary cache unavailable (%s); falling back to standby for this process.",
                exc,
            )
            self._primary_failed = True

    async def get(self, key: str) -> str | None:
        if not self._primary_failed:
            try:
                return await self._primary.get(key)
            except Exception as exc:  # any failure means fall back
                self._demote(exc)
        return await self._standby.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if not self._primary_failed:
            try:
                await self._primary.set(key, value, ttl_seconds)
                return
            except Exception as exc:
                self._demote(exc)
        await self._standby.set(key, value, ttl_seconds)

    async def delete(self, key: str) -> None:
        if not self._primary_failed:
            try:
                await self._primary.delete(key)
            except Exception as exc:
                self._demote(exc)
        await self._standby.delete(key)

    async def close(self) -> None:
        for backend in (self._primary, self._standby):
            try:
                await backend.close()
            except Exception as exc:  # shutdown must not raise
                logger.debug("Error closing cache backend: %s", exc)


__all__ = ["FallbackCache", "NullCache", "PostgresCache", "RedisCache"]
