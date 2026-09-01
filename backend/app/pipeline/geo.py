"""Geographic helpers.

Small, but shared by two callers that must agree: peer density asks "how many
competitors are near this business?" and dedupe asks "are these two records the
same building?". Both are distance questions, and letting them drift apart would
mean the tool contradicted itself about where things are.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


__all__ = ["EARTH_RADIUS_KM", "haversine_km"]
