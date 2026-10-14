"""
Tests for API key authentication on protected endpoints and public endpoint access policy.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from locus.api.dependencies import (
    get_api_key_service,
    get_geocode_service,
)
from locus.domain.api_key_service import (
    APIKeyExpiredError,
    APIKeyRevokedError,
    APIKeyService,
    InvalidAPIKeyError,
    UserInactiveError,
    UserUnverifiedError,
)
from locus.domain.geocode_service import GeocodeService
from locus.domain.models import APIKey, User
from locus.domain.schemas import GeocodeResult, MatchType
from locus.main import app
from locus.security import hash_password, hash_token


@pytest.fixture
def mock_geocode_svc():
    svc = AsyncMock(spec=GeocodeService)
    res = GeocodeResult(
        county_name="Nairobi",
        constituency="Starehe",
        ward_name="Nairobi Central",
        location_name="Nairobi Central",
        sub_location="KICC",
        match_type=MatchType.EXACT,
        distance_m=0.0,
        boundary_version="iebc-2024",
    )
    svc.reverse_geocode.return_value = res
    svc.reverse_geocode_batch.return_value = [res]
    return svc


@pytest.fixture
def mock_key_service():
    return AsyncMock(spec=APIKeyService)


@pytest.fixture
def sample_user():
    return User(
        id=uuid4(),
        email="developer@example.com",
        password_hash=hash_password("Password123!"),
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_api_key(sample_user):
    return APIKey(
        id=uuid4(),
        user_id=sample_user.id,
        name="Test App",
        key_prefix="locus_live_abcd",
        key_hash=hash_token("locus_live_abcd12345"),
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        revoked_at=None,
    )


@pytest.fixture
def client(mock_geocode_svc, mock_key_service):
    app.dependency_overrides[get_geocode_service] = lambda: mock_geocode_svc
    app.dependency_overrides[get_api_key_service] = lambda: mock_key_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestProtectedEndpointsAuth:
    def test_valid_api_key_allows_access(
        self, client, mock_key_service, sample_api_key, sample_user
    ):
        mock_key_service.authenticate_key.return_value = (sample_api_key, sample_user)

        resp = client.get(
            "/v1/protected/geocode/reverse",
            params={"lat": -1.2864, "lon": 36.8172},
            headers={"Authorization": "Bearer locus_live_validkey123"},
        )
        assert resp.status_code == 200
        assert resp.json()["county_name"] == "Nairobi"
        mock_key_service.authenticate_key.assert_awaited_once_with("locus_live_validkey123")

    def test_missing_authorization_header_401(self, client):
        resp = client.get(
            "/v1/protected/geocode/reverse",
            params={"lat": -1.2864, "lon": 36.8172},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "unauthorized"

    def test_malformed_authorization_header_401(self, client):
        resp = client.get(
            "/v1/protected/geocode/reverse",
            params={"lat": -1.2864, "lon": 36.8172},
            headers={"Authorization": "Basic bm90LWJlYXJlcg=="},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "unauthorized"

    def test_invalid_api_key_401(self, client, mock_key_service):
        mock_key_service.authenticate_key.side_effect = InvalidAPIKeyError("Invalid API key")

        resp = client.get(
            "/v1/protected/geocode/reverse",
            params={"lat": -1.2864, "lon": 36.8172},
            headers={"Authorization": "Bearer locus_live_invalidkey"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "invalid_api_key"

    def test_revoked_api_key_401(self, client, mock_key_service):
        mock_key_service.authenticate_key.side_effect = APIKeyRevokedError("Key revoked")

        resp = client.get(
            "/v1/protected/geocode/reverse",
            params={"lat": -1.2864, "lon": 36.8172},
            headers={"Authorization": "Bearer locus_live_revokedkey"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "api_key_revoked"

    def test_expired_api_key_401(self, client, mock_key_service):
        mock_key_service.authenticate_key.side_effect = APIKeyExpiredError("Key expired")

        resp = client.get(
            "/v1/protected/geocode/reverse",
            params={"lat": -1.2864, "lon": 36.8172},
            headers={"Authorization": "Bearer locus_live_expiredkey"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "api_key_expired"

    def test_inactive_user_403(self, client, mock_key_service):
        mock_key_service.authenticate_key.side_effect = UserInactiveError("User inactive")

        resp = client.get(
            "/v1/protected/geocode/reverse",
            params={"lat": -1.2864, "lon": 36.8172},
            headers={"Authorization": "Bearer locus_live_somekey"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "account_inactive"

    def test_unverified_user_403(self, client, mock_key_service):
        mock_key_service.authenticate_key.side_effect = UserUnverifiedError("User unverified")

        resp = client.get(
            "/v1/protected/geocode/reverse",
            params={"lat": -1.2864, "lon": 36.8172},
            headers={"Authorization": "Bearer locus_live_somekey"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "account_unverified"

    def test_protected_batch_success(
        self, client, mock_key_service, sample_api_key, sample_user
    ):
        mock_key_service.authenticate_key.return_value = (sample_api_key, sample_user)

        resp = client.post(
            "/v1/protected/geocode/reverse/batch",
            json={"points": [{"lat": -1.2864, "lon": 36.8172}]},
            headers={"Authorization": "Bearer locus_live_validkey123"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestPublicEndpointsPolicy:
    def test_version_endpoint_is_public(self, client):
        with patch(
            "locus.infrastructure.boundary_repository.BoundaryRepository.get_current_version",
            new_callable=AsyncMock,
        ) as mock_ver:
            mock_ver.return_value = {
                "boundary_version": "iebc-2024",
                "effective_from": None,
            }
            resp = client.get("/v1/boundaries/version")
            # Public metadata endpoint accessible without credentials
            assert resp.status_code == 200
            assert resp.json()["boundary_version"] == "iebc-2024"
