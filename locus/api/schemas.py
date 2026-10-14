"""
Pydantic request/response contracts for the public API.

These are the wire-format types - separate from domain/schemas.py,
which defines internal dataclasses. Keeping them separate means the
JSON shape can evolve (versioned under /v1, /v2) without touching
internal domain logic.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

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


# ── Auth & Account Schemas ──────────────────────────────────────────


class RegisterRequest(BaseModel):
    """Request body for user registration."""

    email: str = Field(..., description="User's email address")
    password: str = Field(..., min_length=8, max_length=128, description="User password (min 8 characters)")


class VerifyEmailRequest(BaseModel):
    """Request body for verifying an email address."""

    token: str = Field(..., description="Cryptographically secure verification token")


class ResendVerificationRequest(BaseModel):
    """Request body to request a new email verification token."""

    email: str = Field(..., description="Registered email address")


class LoginRequest(BaseModel):
    """Request body for user login."""

    email: str = Field(..., description="User's email address")
    password: str = Field(..., description="User's password")


class TokenResponse(BaseModel):
    """Response returned upon successful authentication containing access token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    """Public representation of a user account."""

    id: UUID
    email: str
    is_active: bool
    is_verified: bool
    created_at: datetime


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


# ── API Key Schemas ──────────────────────────────────────────────────


class CreateAPIKeyRequest(BaseModel):
    """Request body for generating a new API key."""

    name: str = Field(..., min_length=1, max_length=100, description="Human-readable name for the API key")
    expires_in_days: int | None = Field(None, ge=1, le=365, description="Optional lifetime in days (1-365)")


class CreatedAPIKeyResponse(BaseModel):
    """Response returned immediately after API key creation. Contains raw key once only."""

    id: UUID
    name: str
    key: str
    key_prefix: str
    created_at: datetime
    expires_at: datetime | None = None


class APIKeyResponse(BaseModel):
    """Safe metadata representation of an API key (never exposes key or hash)."""

    id: UUID
    name: str
    key_prefix: str
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool

