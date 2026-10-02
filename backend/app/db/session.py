"""Async engine and session factory.

One engine per process, created lazily so that importing this module never opens
a socket — tests and the seed collector import the models without a database
running, and should not be punished for it.

The awkward part of this module is `connection_spec`, and it exists because the
URL a hosted Postgres hands you is rarely the URL SQLAlchemy wants:

- Dashboards give `postgres://` or `postgresql://`. The async engine needs the
  driver spelled out (`postgresql+asyncpg://`), and failing on that is a rite of
  passage nobody needs.
- They append `?sslmode=require`, which is a libpq parameter. asyncpg does not
  read it and raises on the unknown keyword, so it is translated to asyncpg's
  own `ssl` argument.
- Supabase's *transaction* pooler (port 6543) is PgBouncer in transaction mode,
  where a prepared statement may be replayed on a different server connection
  than the one that prepared it. asyncpg prepares everything by default, so
  both its statement cache and SQLAlchemy's are disabled there. The session
  pooler (port 5432 on the same host) and a direct connection need none of it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_SUPABASE_HOST_SUFFIXES = (".supabase.co", ".supabase.com")
_PGBOUNCER_TRANSACTION_PORT = 6543


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when something asks for an engine and no DATABASE_URL is set.

    Deliberately not a connection error: callers that can run without a
    database check `database_configured()` first, and anything that reaches
    this has a bug rather than an outage.
    """


@dataclass(frozen=True)
class ConnectionSpec:
    """A normalised URL plus the driver arguments it needs."""

    url: URL
    connect_args: dict[str, Any] = field(default_factory=dict)
    uses_transaction_pooler: bool = False

    @property
    def safe_description(self) -> str:
        """Where we are connecting, without the credentials. For logs."""
        return f"{self.url.host}:{self.url.port or 5432}/{self.url.database}"


def database_configured() -> bool:
    return bool(settings.database_url.strip())


def connection_spec(raw: str | None = None) -> ConnectionSpec:
    """Normalise a Postgres URL into what the async engine needs."""
    raw = (raw if raw is not None else settings.database_url).strip()
    if not raw:
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")

    url = make_url(raw)
    if not url.drivername.startswith(("postgres", "postgresql")):
        raise ValueError(f"DATABASE_URL must be a Postgres URL, got {url.drivername!r}")
    url = url.set(drivername="postgresql+asyncpg")

    query = dict(url.query)
    # asyncpg waits a full minute to connect by default. A wrong host would hold
    # the container's boot for that long before the snapshot fallback could take
    # over, so fail fast instead — and bound any single statement too.
    connect_args: dict[str, Any] = {"timeout": 10, "command_timeout": 30}

    # libpq spelling -> asyncpg spelling. `pgbouncer=true` is a Prisma-ism that
    # Supabase's dashboard also emits; it means nothing to asyncpg.
    sslmode = query.pop("sslmode", None)
    query.pop("pgbouncer", None)
    host = url.host or ""
    is_supabase = host.endswith(_SUPABASE_HOST_SUFFIXES)
    if isinstance(sslmode, str) and sslmode != "disable":
        connect_args["ssl"] = sslmode
    elif is_supabase:
        # Supabase requires TLS; say so rather than rely on the URL carrying it.
        connect_args["ssl"] = "require"

    transaction_pooler = is_supabase and url.port == _PGBOUNCER_TRANSACTION_PORT
    if transaction_pooler:
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_name_func"] = lambda: f"__asyncpg_{uuid4()}__"
        query["prepared_statement_cache_size"] = "0"

    return ConnectionSpec(
        url=url.set(query=query),
        connect_args=connect_args,
        uses_transaction_pooler=transaction_pooler,
    )


@lru_cache
def get_engine() -> AsyncEngine:
    spec = connection_spec()
    return create_async_engine(
        spec.url,
        connect_args=spec.connect_args,
        # A long-running container, not a serverless function: a warm pool is
        # the point. Sized for a hosted free tier, where the pooler caps client
        # connections well below what a default pool would happily open.
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=10,
        echo=settings.debug and settings.environment == "development",
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_engine(),
        expire_on_commit=False,  # results stay usable after the session closes
        autoflush=False,
    )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope. Commits on clean exit, rolls back on any exception."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Same semantics as `session_scope`."""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    """Close the pool. Called from the app's lifespan shutdown."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()


__all__ = [
    "ConnectionSpec",
    "DatabaseNotConfiguredError",
    "connection_spec",
    "database_configured",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "session_scope",
]
