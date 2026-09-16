"""
Pydantic request/response contracts for the public API.

These are the wire-format types - separate from domain/schemas.py,
which defines internal dataclasses. Keeping them separate means the
JSON shape can evolve (versioned under /v1, /v2) without touching
internal domain logic.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from locus.config import get_settings


class MatchType(str, Enum):
    """Wire-format match type: ``"exact"`` or ``"nearest"``."""

    EXACT = "exact"
    NEAREST = "nearest"


class GeocodeRequest(BaseModel):
    """Query-parameter model for a single-point reverse geocode."""

    lat: float = Field(..., ge=-90, le=90, description="Latitude (WGS 84)")
    lon: float = Field(..., ge=-180, le=180, description="Longitude (WGS 84)")


class GeocodeResponse(BaseModel):
    """Response body for a reverse-geocode hit."""

    county_name: str
    constituency: str
    ward_name: str
    sub_county_name: str | None = None
    location_name: str | None = None
    sub_location: str | None = None
    match_type: MatchType
    distance_m: float
    boundary_version: str


class BatchGeocodeRequest(BaseModel):
    """Request body for batch reverse geocode."""

    points: list[GeocodeRequest]

    @field_validator("points")
    @classmethod
    def enforce_max_batch_size(
        cls, v: list[GeocodeRequest]
    ) -> list[GeocodeRequest]:
        settings = get_settings()
        if len(v) > settings.MAX_BATCH_SIZE:
            raise ValueError(
                f"Batch size {len(v)} exceeds the maximum of "
                f"{settings.MAX_BATCH_SIZE}"
            )
        return v


class VersionResponse(BaseModel):
    """Response body for ``GET /boundaries/version``."""

    boundary_version: str
    effective_from: date | None = None
