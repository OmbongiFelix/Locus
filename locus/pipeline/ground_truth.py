"""
Curated landmark list for ground-truth validation of staging data.

Each entry maps a well-known location (lat, lon) to its expected
county and constituency, drawn from official gazetteer / IEBC records.
Used by pipeline/validate_geometry.py to spot-check that a boundary
release is spatially correct before promotion to production.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Landmark:
    """A known point with its expected administrative placement."""

    name: str
    lat: float
    lon: float
    expected_county: str
    expected_constituency: str


# Curated subset — extend as needed for regression coverage.
LANDMARKS: list[Landmark] = [
    Landmark(
        name="KICC (Kenyatta International Convention Centre)",
        lat=-1.2864,
        lon=36.8172,
        expected_county="Nairobi",
        expected_constituency="Starehe",
    ),
    Landmark(
        name="Moi International Airport, Mombasa",
        lat=-4.0348,
        lon=39.5942,
        expected_county="Mombasa",
        expected_constituency="Mvita",
    ),
    Landmark(
        name="Kisumu International Airport",
        lat=-0.0862,
        lon=34.7289,
        expected_county="Kisumu",
        expected_constituency="Kisumu Central",
    ),
    Landmark(
        name="Eldoret Town Centre",
        lat=0.5143,
        lon=35.2698,
        expected_county="Uasin Gishu",
        expected_constituency="Ainabkoi",
    ),
    Landmark(
        name="Nakuru Town Hall",
        lat=-0.3031,
        lon=36.0800,
        expected_county="Nakuru",
        expected_constituency="Nakuru Town East",
    ),
]
