"""The discovery contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.pipeline.markets import Market
from app.schemas import Company


@runtime_checkable
class DiscoverySource(Protocol):
    """Produces candidate companies for a market.

    Sources return whatever they legitimately hold and leave everything else
    unset. A source must never fill a field with a plausible-looking guess to
    look more complete: the scoring engine distinguishes "measured" from
    "unknown", and that distinction is only worth anything if the sources
    respect it.
    """

    #: Short identifier recorded on every company this source produces.
    name: str

    async def discover(self, market: Market) -> list[Company]:
        """Return candidates for the market. Never raises for an empty result."""
        ...


__all__ = ["DiscoverySource"]
