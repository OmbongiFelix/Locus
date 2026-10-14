"""
End-to-end HTTP tests for /api-keys routes using FastAPI TestClient.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from locus.api.dependencies import (
    get_api_key_service,
    get_current_user,
)
from locus.domain.api_key_service import (
    APIKeyNotFoundError,
    APIKeyService,
    InvalidKeyNameError,
)
from locus.domain.models import APIKey, User
from locus.main import app
from locus.security import hash_password, hash_token


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
        name="Production Service",
        key_prefix="locus_live_abcd",
        key_hash=hash_token("locus_live_abcd1234567890"),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        revoked_at=None,
    )


@pytest.fixture
def api_keys_client(mock_key_service, sample_user):
    app.dependency_overrides[get_api_key_service] = lambda: mock_key_service
    app.dependency_overrides[get_current_user] = lambda: sample_user
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestCreateAPIKeyEndpoint:
    def test_create_key_success_201(
        self, api_keys_client, mock_key_service, sample_api_key
    ):
        raw_key = "locus_live_mockedrawkeysecret12345"
        mock_key_service.create_key.return_value = (sample_api_key, raw_key)

        resp = api_keys_client.post(
            "/v1/api-keys",
            json={"name": "Production Service", "expires_in_days": 30},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == str(sample_api_key.id)
        assert body["name"] == "Production Service"
        assert body["key"] == raw_key
        assert body["key_prefix"] == "locus_live_abcd"
        assert "key_hash" not in body

    def test_create_key_invalid_name_400(self, api_keys_client, mock_key_service):
        mock_key_service.create_key.side_effect = InvalidKeyNameError("Name cannot be empty")

        resp = api_keys_client.post(
            "/v1/api-keys",
            json={"name": "  "},
        )
        assert resp.status_code in (400, 422)

    def test_create_key_unauthenticated_401(self, mock_key_service):
        # Override only service, not get_current_user — should require auth
        app.dependency_overrides[get_api_key_service] = lambda: mock_key_service
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/v1/api-keys", json={"name": "Test Key"})
        assert resp.status_code == 401
        app.dependency_overrides.clear()


class TestListAPIKeysEndpoint:
    def test_list_keys_success_200(
        self, api_keys_client, mock_key_service, sample_api_key
    ):
        mock_key_service.list_keys.return_value = [sample_api_key]

        resp = api_keys_client.get("/v1/api-keys")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        item = body[0]
        assert item["id"] == str(sample_api_key.id)
        assert item["name"] == "Production Service"
        assert item["key_prefix"] == "locus_live_abcd"
        assert item["is_active"] is True
        # Secrets MUST NOT be exposed
        assert "key" not in item
        assert "key_hash" not in item


class TestGetAPIKeyEndpoint:
    def test_get_key_success_200(
        self, api_keys_client, mock_key_service, sample_api_key
    ):
        mock_key_service.get_key.return_value = sample_api_key

        resp = api_keys_client.get(f"/v1/api-keys/{sample_api_key.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(sample_api_key.id)
        assert "key" not in body
        assert "key_hash" not in body

    def test_get_key_not_found_404(self, api_keys_client, mock_key_service):
        mock_key_service.get_key.side_effect = APIKeyNotFoundError("Not found")

        resp = api_keys_client.get(f"/v1/api-keys/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "not_found"


class TestRevokeAPIKeyEndpoint:
    def test_revoke_key_success_200(
        self, api_keys_client, mock_key_service, sample_api_key
    ):
        revoked_key = APIKey(
            id=sample_api_key.id,
            user_id=sample_api_key.user_id,
            name=sample_api_key.name,
            key_prefix=sample_api_key.key_prefix,
            key_hash=sample_api_key.key_hash,
            created_at=sample_api_key.created_at,
            expires_at=sample_api_key.expires_at,
            revoked_at=datetime.now(timezone.utc),
        )
        mock_key_service.revoke_key.return_value = revoked_key

        resp = api_keys_client.delete(f"/v1/api-keys/{sample_api_key.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(sample_api_key.id)
        assert body["is_active"] is False
        assert body["revoked_at"] is not None

    def test_revoke_key_not_found_or_other_user_404(
        self, api_keys_client, mock_key_service
    ):
        mock_key_service.revoke_key.side_effect = APIKeyNotFoundError("Not found")

        resp = api_keys_client.delete(f"/v1/api-keys/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "not_found"
