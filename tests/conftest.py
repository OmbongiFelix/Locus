"""Shared test configuration and fixtures."""

import pytest


@pytest.fixture
def sample_row() -> dict:
    """A typical boundary row dict as returned by the repository."""
    return {
        "county_name": "Nairobi",
        "constituency": "Starehe",
        "ward_name": "Nairobi Central",
        "sub_county_name": None,
        "location_name": "Nairobi Central",
        "sub_location": "KICC",
        "boundary_version": "iebc-2024",
        "distance_m": 0.0,
    }
