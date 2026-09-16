"""
Internal data contracts for the domain layer.

Separate from api/schemas.py (the public wire format) so the two can
evolve independently. No Pydantic, no FastAPI - plain dataclasses so
this module is importable from pipeline/ scripts without pulling in

the web framework.
"""

from dataclasses import dataclass
from enum import Enum


class MatchType(Enum):
    """Match type for a reverse-geocode result.

    Mirrored (not imported) from api/schemas.py to avoid a
    domain → api dependency.
    """

    EXACT = "exact"
    NEAREST = "nearest"


@dataclass(frozen=True)
class GeocodeResult:
    """Immutable value object representing a single reverse-geocode result."""

    county_name: str
    constituency: str
    ward_name: str
    sub_county_name: str     # i added this to support sub-county level reverse geocoding
    location_name: str | None
    sub_location: str | None
    match_type: MatchType
    distance_m: float
    boundary_version: str
