"""
End-to-end API tests using FastAPI's TestClient.

Exercises the HTTP layer (api/routes.py) with the geocode service
dependency overridden to a mock, covering the request/response
contract and error codes defined in the design document (Section 5).
"""

import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from locus.main import app
from locus.api.dependencies import (
    get_geocode_service,
    verify_api_key,
    rate_limiter,
    _rate_limit_state,
)
from locus.domain.geocode_service import GeocodeService, NoMatchError
from locus.domain.validation import InvalidCoordinatesError
from locus.domain.schemas import GeocodeResult, MatchType


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def nairobi_result():
    return GeocodeResult(
        county_name="Nairobi",
        constituency="Starehe",
        ward_name="Nairobi Central",
        location_name="Nairobi Central",
        sub_location="KICC",
        match_type=MatchType.EXACT,
        distance_m=0.0,
        boundary_version="iebc-2024",
    )


@pytest.fixture
def mock_service(nairobi_result):
    svc = AsyncMock(spec=GeocodeService)
    svc.reverse_geocode = AsyncMock(return_value=nairobi_result)
    svc.reverse_geocode_batch = AsyncMock(return_value=[nairobi_result])
    return svc


@pytest.fixture
def client(mock_service):
    """TestClient with service, auth, and rate-limiter dependencies
    overridden so tests run without DB / Redis."""
    app.dependency_overrides[get_geocode_service] = lambda: mock_service
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    app.dependency_overrides[rate_limiter] = lambda: None
    _rate_limit_state.clear()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ── Test classes ─────────────────────────────────────────────────────


class TestReverseGeocodeEndpoint:
    def test_valid_coordinates_200(self, client):
        resp = client.get(
            "/v1/geocode/reverse", params={"lat": -1.2864, "lon": 36.8172}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["county_name"] == "Nairobi"
        assert body["match_type"] == "exact"

    def test_out_of_range_400(self, client, mock_service):
        mock_service.reverse_geocode.side_effect = InvalidCoordinatesError(
            "out of range"
        )
        resp = client.get(
            "/v1/geocode/reverse", params={"lat": -5.0, "lon": 36.0}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_coordinates"

    def test_no_match_404(self, client, mock_service):
        mock_service.reverse_geocode.side_effect = NoMatchError("no match")
        resp = client.get(
            "/v1/geocode/reverse", params={"lat": -1.0, "lon": 34.5}
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "no_match"


class TestBatchEndpoint:
    def test_batch_over_size_cap_422(self, client):
        """Exceeding MAX_BATCH_SIZE is rejected by the Pydantic
        field_validator (returns 422)."""
        points = [{"lat": -1.0, "lon": 36.0}] * 51
        resp = client.post(
            "/v1/geocode/reverse/batch", json={"points": points}
        )
        # Pydantic validation error surfaces as 422
        assert resp.status_code == 422


class TestAuthAndRateLimiting:
    def test_missing_api_key_422(self, mock_service):
        """Request without ``X-API-Key`` header → 422 (missing required
        header) when API keys are configured."""
        app.dependency_overrides[get_geocode_service] = lambda: mock_service
        app.dependency_overrides[rate_limiter] = lambda: None
        # Don't override verify_api_key — let it run for real
        with patch("locus.api.dependencies.get_settings") as mock_settings:
            mock_settings.return_value.API_KEYS = "real-key-1,real-key-2"
            mock_settings.return_value.RATE_LIMIT_PER_MINUTE = 60
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get(
                "/v1/geocode/reverse", params={"lat": -1.0, "lon": 36.0}
            )
            assert resp.status_code == 422  # missing required header
        app.dependency_overrides.clear()

    def test_invalid_api_key_401(self, mock_service):
        """Request with a wrong ``X-API-Key`` → 401."""
        app.dependency_overrides[get_geocode_service] = lambda: mock_service
        app.dependency_overrides[rate_limiter] = lambda: None
        with patch("locus.api.dependencies.get_settings") as mock_settings:
            mock_settings.return_value.API_KEYS = "real-key-1"
            mock_settings.return_value.RATE_LIMIT_PER_MINUTE = 60
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get(
                "/v1/geocode/reverse",
                params={"lat": -1.0, "lon": 36.0},
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status_code == 401
        app.dependency_overrides.clear()

    def test_rate_limit_429(self, mock_service):
        """Repeated requests beyond the rate limit → 429 with
        ``Retry-After`` header."""
        app.dependency_overrides[get_geocode_service] = lambda: mock_service
        app.dependency_overrides[verify_api_key] = lambda: "test-key"
        _rate_limit_state.clear()

        with patch("locus.api.dependencies.get_settings") as mock_settings:
            mock_settings.return_value.RATE_LIMIT_PER_MINUTE = 2
            mock_settings.return_value.API_KEYS = ""
            c = TestClient(app, raise_server_exceptions=False)

            # First 2 requests pass
            for _ in range(2):
                c.get(
                    "/v1/geocode/reverse",
                    params={"lat": -1.2864, "lon": 36.8172},
                    headers={"X-API-Key": "test-key"},
                )

            # Third should be rate-limited
            resp = c.get(
                "/v1/geocode/reverse",
                params={"lat": -1.2864, "lon": 36.8172},
                headers={"X-API-Key": "test-key"},
            )
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
        app.dependency_overrides.clear()


class TestHealthEndpoint:
    def test_health_returns_response(self, client):
        """GET /v1/health returns a response (200 or 503 depending on
        whether infra is available)."""
        resp = client.get("/v1/health")
        assert resp.status_code in (200, 503)
