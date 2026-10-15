"""
Regression tests for confirmed defects found during code review.

Each test is labelled with the issue it guards against so it is easy
to identify its purpose during future triage.
"""

import pytest
from unittest.mock import AsyncMock

from locus.domain.schemas import GeocodeResult, MatchType
from locus.infrastructure.cache import _serialize, _deserialize


# ── Regression: cache serialization round-trip (sub_county_name) ─────────────
# Bug: _serialize() omitted sub_county_name but _deserialize() read it via
# obj["sub_county_name"], causing a KeyError on every cache hit after the
# field was added to the domain model.


class TestCacheSerializationRoundTrip:
    def _make_result(self, *, sub_county_name=None) -> GeocodeResult:
        return GeocodeResult(
            county_name="Nairobi",
            constituency="Starehe",
            ward_name="Nairobi Central",
            sub_county_name=sub_county_name,
            location_name="Nairobi Central",
            sub_location="KICC",
            match_type=MatchType.EXACT,
            distance_m=0.0,
            boundary_version="iebc-2024",
        )

    def test_round_trip_without_sub_county(self):
        """sub_county_name=None must survive serialize → deserialize."""
        result = self._make_result(sub_county_name=None)
        serialized = _serialize(result)
        restored = _deserialize(serialized)
        assert restored == result
        assert restored.sub_county_name is None

    def test_round_trip_with_sub_county(self):
        """sub_county_name present must survive serialize → deserialize."""
        result = self._make_result(sub_county_name="Westlands")
        serialized = _serialize(result)
        restored = _deserialize(serialized)
        assert restored == result
        assert restored.sub_county_name == "Westlands"

    def test_serialized_json_contains_sub_county_name_key(self):
        """Serialized JSON must always contain the sub_county_name key."""
        import json

        result = self._make_result(sub_county_name=None)
        obj = json.loads(_serialize(result))
        assert "sub_county_name" in obj


# ── Regression: global exception handler must not swallow HTTPException ───────
# Bug: the bare `Exception` handler intercepted FastAPI's HTTPException
# (a subclass of Exception), converting properly raised 401/404/422 responses
# into 500 Internal Server Error.


class TestGlobalExceptionHandlerDoesNotSwallowHTTPException:
    def test_http_exception_propagates_correctly(self):
        """A route that raises HTTPException 401 must return 401, not 500."""
        from fastapi.testclient import TestClient
        from locus.main import app
        from locus.api.dependencies import get_current_user_from_api_key

        # Simulate the dependency raising HTTPException(401)
        from fastapi import HTTPException

        def _raise_401():
            raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "test"})

        app.dependency_overrides[get_current_user_from_api_key] = _raise_401
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/v1/protected/geocode/reverse",
                params={"lat": -1.2864, "lon": 36.8172},
            )
            assert resp.status_code == 401, (
                f"Expected 401 but got {resp.status_code}. "
                "The global Exception handler may be swallowing HTTPException."
            )
        finally:
            app.dependency_overrides.clear()
