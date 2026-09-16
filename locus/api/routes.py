"""
HTTP route definitions for the Locus reverse-geocoding API.

Endpoints:
GET  /v1/geocode/reverse        - single-point reverse geocode
POST /v1/geocode/reverse/batch  - batch reverse geocode (capped size)
GET  /v1/boundaries/version     - currently active boundary_version
GET  /v1/health                 - liveness/readiness probe

Routes are thin: they parse/validate the HTTP layer via schemas.py,
call into domain/geocode_service.py, and translate the result (or a
raised domain exception) into an HTTP response. No spatial logic here.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from locus.api.schemas import (
    BatchGeocodeRequest,
    GeocodeRequest,
    GeocodeResponse,
    VersionResponse,
    MatchType as APIMatchType,
)
from locus.api.dependencies import get_geocode_service, rate_limiter, verify_api_key
from locus.domain.geocode_service import GeocodeService, NoMatchError
from locus.domain.validation import InvalidCoordinatesError
from locus.infrastructure.boundary_repository import BoundaryRepository

logger = logging.getLogger(__name__)

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────


def _to_response(result) -> GeocodeResponse:
    """Map a domain GeocodeResult to the public API wire type."""
    return GeocodeResponse(
        county_name=result.county_name,
        constituency=result.constituency,
        ward_name=result.ward_name,
        sub_county_name=result.sub_county_name,
        location_name=result.location_name,
        sub_location=result.sub_location,
        match_type=APIMatchType(result.match_type.value),
        distance_m=result.distance_m,
        boundary_version=result.boundary_version,
    )


# ── endpoints ────────────────────────────────────────────────────────


@router.get("/geocode/reverse", response_model=GeocodeResponse)
async def reverse_geocode(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    service: GeocodeService = Depends(get_geocode_service),
    _rate: None = Depends(rate_limiter),
) -> GeocodeResponse:
    """Single-point reverse geocode."""
    start = time.perf_counter()
    try:
        result = await service.reverse_geocode(lat, lon)
    except InvalidCoordinatesError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_coordinates", "message": str(exc)},
        )
    except NoMatchError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "no_match", "message": str(exc)},
        )

    latency = (time.perf_counter() - start) * 1000
    logger.info(
        "reverse_geocode",
        extra={
            "path": "/v1/geocode/reverse",
            "latency_ms": round(latency, 2),
            "status_code": 200,
            "match_type": result.match_type.value,
        },
    )
    return _to_response(result)


@router.post("/geocode/reverse/batch", response_model=list[GeocodeResponse])
async def reverse_geocode_batch(
    body: BatchGeocodeRequest,
    service: GeocodeService = Depends(get_geocode_service),
    _rate: None = Depends(rate_limiter),
) -> list[GeocodeResponse]:
    """Batch reverse geocode (capped at ``MAX_BATCH_SIZE``)."""
    try:
        points = [(p.lat, p.lon) for p in body.points]
        results = await service.reverse_geocode_batch(points)
    except InvalidCoordinatesError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_coordinates", "message": str(exc)},
        )
    except NoMatchError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "no_match", "message": str(exc)},
        )
    return [_to_response(r) for r in results]


@router.get("/boundaries/version", response_model=VersionResponse)
async def get_boundary_version() -> VersionResponse:
    """Return the active ``boundary_version`` and ``effective_from`` date."""
    repo = BoundaryRepository()
    info = await repo.get_current_version()
    return VersionResponse(
        boundary_version=info["boundary_version"],
        effective_from=info.get("effective_from"),
    )


@router.get("/health")
async def health() -> dict:
    """Liveness/readiness probe — 200 once DB and cache are reachable."""
    from locus.infrastructure.db import get_connection
    from locus.infrastructure.cache import _redis_client

    errors: list[str] = []

    try:
        async with get_connection() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        errors.append(f"db: {exc}")

    try:
        if _redis_client is not None:
            await _redis_client.ping()
        else:
            errors.append("cache: Redis client not initialised")
    except Exception as exc:
        errors.append(f"cache: {exc}")

    if errors:
        raise HTTPException(
            status_code=503,
            detail={"status": "unhealthy", "errors": errors},
        )

    return {"status": "healthy"}
