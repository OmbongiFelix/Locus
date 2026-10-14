"""
End-to-end HTTP tests for /auth routes using FastAPI TestClient.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from locus.api.dependencies import (
    _auth_rate_limit_state,
    get_auth_service,
    get_user_repository,
)
from locus.domain.auth_service import (
    AccountInactiveError,
    AccountUnverifiedError,
    AuthService,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    InvalidEmailError,
    InvalidTokenError,
    TokenAlreadyUsedError,
    TokenExpiredError,
    WeakPasswordError,
)
from locus.domain.models import User
from locus.infrastructure.user_repository import UserRepository
from locus.main import app
from locus.security import create_access_token, hash_password


@pytest.fixture
def mock_auth_service():
    return AsyncMock(spec=AuthService)


@pytest.fixture
def mock_user_repo():
    return AsyncMock(spec=UserRepository)


@pytest.fixture
def verified_user():
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
def auth_client(mock_auth_service, mock_user_repo):
    app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
    app.dependency_overrides[get_user_repository] = lambda: mock_user_repo
    _auth_rate_limit_state.clear()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestRegisterEndpoint:
    def test_register_success_201(self, auth_client, mock_auth_service, verified_user):
        unverified = User(
            id=verified_user.id,
            email=verified_user.email,
            password_hash=verified_user.password_hash,
            is_active=True,
            email_verified_at=None,
            created_at=verified_user.created_at,
            updated_at=verified_user.updated_at,
        )
        mock_auth_service.register.return_value = (unverified, "mock-token-12345")

        resp = auth_client.post(
            "/v1/auth/register",
            json={"email": "developer@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == "developer@example.com"
        assert body["is_verified"] is False
        assert "password" not in body
        assert "password_hash" not in body

    def test_register_duplicate_409(self, auth_client, mock_auth_service):
        mock_auth_service.register.side_effect = EmailAlreadyExistsError("Already registered")

        resp = auth_client.post(
            "/v1/auth/register",
            json={"email": "existing@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "email_already_registered"

    def test_register_short_password_422_or_400(self, auth_client, mock_auth_service):
        # Pydantic schema validation rejects passwords < 8 chars with 422
        resp = auth_client.post(
            "/v1/auth/register",
            json={"email": "user@example.com", "password": "short"},
        )
        assert resp.status_code in (400, 422)


class TestVerifyEmailEndpoint:
    def test_verify_email_success_200(self, auth_client, mock_auth_service, verified_user):
        mock_auth_service.verify_email.return_value = verified_user

        resp = auth_client.post(
            "/v1/auth/verify-email",
            json={"token": "valid-token-12345"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_verified"] is True

    def test_verify_email_invalid_token_400(self, auth_client, mock_auth_service):
        mock_auth_service.verify_email.side_effect = InvalidTokenError("Invalid token")

        resp = auth_client.post(
            "/v1/auth/verify-email",
            json={"token": "bogus-token"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_token"

    def test_verify_email_expired_token_400(self, auth_client, mock_auth_service):
        mock_auth_service.verify_email.side_effect = TokenExpiredError("Expired token")

        resp = auth_client.post(
            "/v1/auth/verify-email",
            json={"token": "expired-token"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "token_expired"

    def test_verify_email_already_used_400(self, auth_client, mock_auth_service):
        mock_auth_service.verify_email.side_effect = TokenAlreadyUsedError("Used token")

        resp = auth_client.post(
            "/v1/auth/verify-email",
            json={"token": "used-token"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "token_already_used"


class TestResendVerificationEndpoint:
    def test_resend_verification_200(self, auth_client, mock_auth_service):
        mock_auth_service.resend_verification.return_value = True

        resp = auth_client.post(
            "/v1/auth/resend-verification",
            json={"email": "developer@example.com"},
        )
        assert resp.status_code == 200
        assert "message" in resp.json()


class TestLoginEndpoint:
    def test_login_success_200(self, auth_client, mock_auth_service, verified_user):
        mock_auth_service.authenticate.return_value = verified_user

        resp = auth_client.post(
            "/v1/auth/login",
            json={"email": "developer@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

    def test_login_invalid_credentials_401(self, auth_client, mock_auth_service):
        mock_auth_service.authenticate.side_effect = InvalidCredentialsError("Bad credentials")

        resp = auth_client.post(
            "/v1/auth/login",
            json={"email": "developer@example.com", "password": "WrongPassword!"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "invalid_credentials"

    def test_login_unverified_account_403(self, auth_client, mock_auth_service):
        mock_auth_service.authenticate.side_effect = AccountUnverifiedError("Unverified email")

        resp = auth_client.post(
            "/v1/auth/login",
            json={"email": "developer@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "account_unverified"

    def test_login_inactive_account_403(self, auth_client, mock_auth_service):
        mock_auth_service.authenticate.side_effect = AccountInactiveError("Inactive account")

        resp = auth_client.post(
            "/v1/auth/login",
            json={"email": "developer@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "account_inactive"


class TestAuthMeEndpoint:
    def test_auth_me_with_valid_bearer_token(
        self, auth_client, mock_user_repo, verified_user
    ):
        mock_user_repo.get_by_id.return_value = verified_user
        token = create_access_token(data={"sub": str(verified_user.id)})

        resp = auth_client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(verified_user.id)
        assert body["email"] == verified_user.email

    def test_auth_me_missing_token_401(self, auth_client):
        resp = auth_client.get("/v1/auth/me")
        assert resp.status_code == 401

    def test_auth_me_invalid_token_401(self, auth_client):
        resp = auth_client.get(
            "/v1/auth/me",
            headers={"Authorization": "Bearer invalid-garbage-token"},
        )
        assert resp.status_code == 401
