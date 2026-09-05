"""
Request-scoped FastAPI dependencies: service construction, auth, and
rate limiting.

Centralizing these here means routes.py stays declarative - each
route just lists which dependencies it needs.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Depends, Header, HTTPException

from locus.config import get_settings
from locus.domain.geocode_service import GeocodeService
from locus.infrastructure.boundary_repository import BoundaryRepository
from locus.infrastructure.cache import GeocodeCache

# In-memory per-key rate-limit state.
# For multi-process production deployments, swap this for a
# Redis-backed sliding-window counter.
_rate_limit_state: dict[str, list[float]] = defaultdict(list)


def get_geocode_service() -> GeocodeService:
    """Construct a GeocodeService wired with its repository and cache.

    Reuses the single DB pool and Redis client that were opened at
    startup — never opens new connections per request.
    """
    return GeocodeService(
        repository=BoundaryRepository(),
        cache=GeocodeCache(),
    )


async def verify_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> str:
    """Validate the API key from the ``X-API-Key`` header.

    If ``settings.API_KEYS`` is empty, all keys are accepted (dev mode).
    """
    settings = get_settings()
    valid_keys = [k.strip() for k in settings.API_KEYS.split(",") if k.strip()]
    if not valid_keys:
        # No keys configured — open access (development only)
        return x_api_key
    if x_api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key


async def rate_limiter(
    api_key: str = Depends(verify_api_key),
) -> None:
    """Enforce ``settings.RATE_LIMIT_PER_MINUTE`` per API key.

    Uses a simple sliding-window counter.  Raises 429 with a
    ``Retry-After`` header when the limit is exceeded.
    """
    settings = get_settings()
    now = time.time()
    window_start = now - 60.0

    # Prune expired timestamps
    timestamps = _rate_limit_state[api_key]
    _rate_limit_state[api_key] = [ts for ts in timestamps if ts > window_start]

    if len(_rate_limit_state[api_key]) >= settings.RATE_LIMIT_PER_MINUTE:
        oldest = min(_rate_limit_state[api_key])
        retry_after = int(60 - (now - oldest)) + 1
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    _rate_limit_state[api_key].append(now)
