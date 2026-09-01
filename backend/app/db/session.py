"""Async engine and session factory.

One engine per process, created lazily so that importing this module never opens
a socket — tests and the seed collector import the models without a database
running, and should not be punished for it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        # A long-running container, not a serverless function: a warm pool is
        # the point. This is the concrete reason the backend is not on Lambda.
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
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


__all__ = [
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "session_scope",
]
