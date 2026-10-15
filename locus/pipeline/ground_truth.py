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
    # TODO: "Mvita" is confirmed wrong (no source supports it) but not
    # yet replaced. wards.shp places this point ~240m inside Jomvu/
    # Miritini — solidly interior, not a boundary sliver — but
    # Changamwe's own ward list ("Airport" ward, under Port Reitz
    # sub-location) is well-corroborated too. The Jomvu/Changamwe line
    # near Miritini was the subject of a 2012 High Court boundary
    # ruling, so this may be a genuinely contested area rather than a
    # simple error. Before resolving: get a verified coordinate for
    # the airport terminal specifically (this lat/lon is several
    # hundred metres from Port Reitz's own commonly-cited coordinates)
    # and re-run the boundary-distance check against that.
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
        expected_constituency="Kisumu West",
    ),
    # Kapseret/Kipkenyo, ~451m inside — Kipkenyo ward was part of the
    # old pre-2012 "Eldoret South" constituency, which dissolved mostly
    # into Kesses (56%) and Kapseret (39%) in the 2012 delimitation.
    # Kapseret getting the larger share is consistent with this result.
    # "Ainabkoi" (prior value) had no support from any source checked.
    Landmark(
        name="Eldoret Town Centre",
        lat=0.5143,
        lon=35.2698,
        expected_county="Uasin Gishu",
        expected_constituency="Kapseret",
    ),
    Landmark(
        name="Nakuru Town Hall",
        lat=-0.3031,
        lon=36.0800,
        expected_county="Nakuru",
        expected_constituency="Nakuru Town East",
    ),
]