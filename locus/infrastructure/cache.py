"""
Redis read-through cache for geocode results.

Implements the caching strategy from the design document (Section 7):
cache keyed by coordinates rounded to 6 decimal places, with the

entire cache invalidated on every boundary_version publish rather
than per-key invalidation.
"""

from __future__ import annotations

from typing import cast
import json
import logging

import redis.asyncio as redis

from locus.config import get_settings
from locus.domain.schemas import GeocodeResult, MatchType

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


# ── Module-level lifecycle (called from main.py) ─────────────────────


async def init_cache() -> None:
    """Open the process-wide Redis client."""
    global _redis_client
    settings = get_settings()
    _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


async def close_cache() -> None:
    """Close the Redis client at shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


# ── Helpers ──────────────────────────────────────────────────────────


def _cache_key(lat: float, lon: float) -> str:
    """Deterministic key from coordinates rounded to 6 decimal places."""
    return f"locus:geocode:{round(lat, 6):.6f},{round(lon, 6):.6f}"


def _serialize(result: GeocodeResult) -> str:
    """Serialize a GeocodeResult to JSON for storage."""
    return json.dumps(
        {
            "county_name": result.county_name,
            "constituency": result.constituency,
            "ward_name": result.ward_name,
            "sub_county_name": result.sub_county_name,
            "location_name": result.location_name,
            "sub_location": result.sub_location,
            "match_type": result.match_type.value,
            "distance_m": result.distance_m,
            "boundary_version": result.boundary_version,
        }
    )


def _deserialize(data: str) -> GeocodeResult:
    """Reconstruct a GeocodeResult from cached JSON."""
    obj = json.loads(data)
    return GeocodeResult(
        county_name=obj["county_name"],
        constituency=obj["constituency"],
        ward_name=obj["ward_name"],
        sub_county_name=obj["sub_county_name"],
        location_name=obj["location_name"],
        sub_location=obj["sub_location"],
        match_type=MatchType(obj["match_type"]),
        distance_m=obj["distance_m"],
        boundary_version=obj["boundary_version"],
    )


# ── Public cache interface ───────────────────────────────────────────


class GeocodeCache:
    """Read-through cache backed by the process-wide Redis client.

    Every method fails open — a Redis outage never blocks a request.
    """

    async def get(self, lat: float, lon: float) -> GeocodeResult | None:
        """Look up a cached result.  Returns None on miss or Redis error."""
        if _redis_client is None:
            return None
        try:
            data = await _redis_client.get(_cache_key(lat, lon))
            if data is not None:
                return _deserialize(cast(str, data))
        except Exception:
            logger.warning("Cache read failed — proceeding without cache", exc_info=True)
        return None

    async def set(
        self,
        lat: float,
        lon: float,
        result: GeocodeResult,
        ttl: int | None = None,
    ) -> None:
        """Write a result with a bounded TTL (safety net even without flush)."""
        if _redis_client is None:
            return
        if ttl is None:
            ttl = get_settings().CACHE_TTL_SECONDS
        try:
            await _redis_client.set(_cache_key(lat, lon), _serialize(result), ex=ttl)
        except Exception:
            logger.warning("Cache write failed", exc_info=True)

    async def flush_all(self) -> None:
        """Flush the entire cache — called by pipeline/publish.py after a version swap."""
        if _redis_client is None:
            return
        try:
            await _redis_client.flushdb()
        except Exception:
            logger.warning("Cache flush failed", exc_info=True)
