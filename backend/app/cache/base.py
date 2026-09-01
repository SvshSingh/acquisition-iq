"""The cache contract.

Deliberately one small interface with three implementations behind it. Redis is
what production wants; Postgres is what keeps the demo alive when there is no
Redis; the null cache is what makes tests deterministic. Callers never branch on
which one they have.

Values are opaque strings. Structured payloads are serialised by the caller, so
the backends stay dumb and interchangeable.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable


@runtime_checkable
class Cache(Protocol):
    """Async key/value cache with per-entry TTL."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def close(self) -> None: ...


def http_cache_key(method: str, url: str) -> str:
    """Stable key for an HTTP response.

    Hashed rather than stored raw: URLs routinely exceed the length of a sane
    index key, and some carry query strings we would rather not have sitting in
    plain text in a shared cache.
    """
    digest = hashlib.sha256(f"{method.upper()} {url}".encode()).hexdigest()
    return f"http:{digest[:48]}"


class NullCache:
    """Stores nothing, always misses.

    Used in tests and whenever caching is explicitly disabled. Having a real
    object rather than `None` means no call site needs a null check.
    """

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def close(self) -> None:
        return None


__all__ = ["Cache", "NullCache", "http_cache_key"]
