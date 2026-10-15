"""
Unit tests for locus/domain/api_key_service.py.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from locus.domain.api_key_service import (
    APIKeyExpiredError,
    APIKeyNotFoundError,
    APIKeyRevokedError,
    APIKeyService,
    InvalidAPIKeyError,
    InvalidKeyNameError,
    UnverifiedTrialLimitReachedError,
    UserInactiveError,
    UserUnverifiedError,
)
from locus.domain.models import APIKey, User
from locus.infrastructure.api_key_repository import APIKeyRepository
from locus.infrastructure.email_service import MockEmailProvider
from locus.infrastructure.user_repository import UserRepository
from locus.security import hash_password, hash_token


@pytest.fixture
def mock_api_key_repo():
    return AsyncMock(spec=APIKeyRepository)


@pytest.fixture
def mock_user_repo():
    repo = AsyncMock(spec=UserRepository)
    repo.increment_unverified_request_count = AsyncMock(return_value=1)
    repo.create_verification_token = AsyncMock()
    repo.invalidate_pending_tokens = AsyncMock()
    return repo


@pytest.fixture
def mock_email_provider():
    return MockEmailProvider()


@pytest.fixture
def api_key_service(mock_api_key_repo, mock_user_repo, mock_email_provider):
    return APIKeyService(
        repository=mock_api_key_repo,
        user_repository=mock_user_repo,
        email_provider=mock_email_provider,
    )


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
        name="Production Key",
        key_prefix="locus_live_abcd",
        key_hash=hash_token("locus_live_abcd1234567890"),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        revoked_at=None,
    )


class TestAPIKeyCreation:
    @pytest.mark.asyncio
    async def test_create_key_success(
        self, api_key_service, mock_api_key_repo, sample_user, sample_api_key
    ):
        mock_api_key_repo.create_api_key.return_value = sample_api_key

        key, raw_key = await api_key_service.create_key(
            user_id=sample_user.id,
            name="Production Key",
            expires_in_days=30,
        )

        assert raw_key.startswith("locus_live_")
        assert key.name == "Production Key"
        mock_api_key_repo.create_api_key.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_key_name_rejected(self, api_key_service, sample_user):
        with pytest.raises(InvalidKeyNameError):
            await api_key_service.create_key(user_id=sample_user.id, name="   ")

    @pytest.mark.asyncio
    async def test_invalid_expiry_rejected(self, api_key_service, sample_user):
        with pytest.raises(ValueError):
            await api_key_service.create_key(
                user_id=sample_user.id, name="Test Key", expires_in_days=0
            )


class TestAPIKeyListingAndRetrieval:
    @pytest.mark.asyncio
    async def test_list_keys(
        self, api_key_service, mock_api_key_repo, sample_user, sample_api_key
    ):
        mock_api_key_repo.list_by_user_id.return_value = [sample_api_key]

        keys = await api_key_service.list_keys(sample_user.id)
        assert len(keys) == 1
        assert keys[0].id == sample_api_key.id
        mock_api_key_repo.list_by_user_id.assert_awaited_once_with(sample_user.id)

    @pytest.mark.asyncio
    async def test_get_key_success(
        self, api_key_service, mock_api_key_repo, sample_user, sample_api_key
    ):
        mock_api_key_repo.get_by_id_and_user_id.return_value = sample_api_key

        key = await api_key_service.get_key(sample_api_key.id, sample_user.id)
        assert key.id == sample_api_key.id

    @pytest.mark.asyncio
    async def test_get_other_users_key_raises_not_found(
        self, api_key_service, mock_api_key_repo
    ):
        mock_api_key_repo.get_by_id_and_user_id.return_value = None

        with pytest.raises(APIKeyNotFoundError):
            await api_key_service.get_key(uuid4(), uuid4())


class TestAPIKeyRevocation:
    @pytest.mark.asyncio
    async def test_revoke_key_success(
        self, api_key_service, mock_api_key_repo, sample_user, sample_api_key
    ):
        revoked_key = APIKey(
            id=sample_api_key.id,
            user_id=sample_user.id,
            name=sample_api_key.name,
            key_prefix=sample_api_key.key_prefix,
            key_hash=sample_api_key.key_hash,
            created_at=sample_api_key.created_at,
            expires_at=sample_api_key.expires_at,
            revoked_at=datetime.now(timezone.utc),
        )
        mock_api_key_repo.revoke_api_key.return_value = revoked_key

        result = await api_key_service.revoke_key(sample_api_key.id, sample_user.id)
        assert result.is_revoked is True
        mock_api_key_repo.revoke_api_key.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_or_other_users_key_raises_not_found(
        self, api_key_service, mock_api_key_repo
    ):
        mock_api_key_repo.revoke_api_key.return_value = None

        with pytest.raises(APIKeyNotFoundError):
            await api_key_service.revoke_key(uuid4(), uuid4())


class TestAPIKeyAuthentication:
    @pytest.mark.asyncio
    async def test_authenticate_valid_key(
        self, api_key_service, mock_api_key_repo, sample_user, sample_api_key
    ):
        raw_key = "locus_live_testrawkey12345"
        sample_api_key.key_hash = hash_token(raw_key)
        mock_api_key_repo.get_by_hash.return_value = (sample_api_key, sample_user)

        key, user = await api_key_service.authenticate_key(raw_key)
        assert key.id == sample_api_key.id
        assert user.id == sample_user.id

    @pytest.mark.asyncio
    async def test_authenticate_unknown_key_raises(
        self, api_key_service, mock_api_key_repo
    ):
        mock_api_key_repo.get_by_hash.return_value = None

        with pytest.raises(InvalidAPIKeyError):
            await api_key_service.authenticate_key("locus_live_nonexistentkey")

    @pytest.mark.asyncio
    async def test_authenticate_revoked_key_raises(
        self, api_key_service, mock_api_key_repo, sample_user, sample_api_key
    ):
        raw_key = "locus_live_revokedkey"
        sample_api_key.key_hash = hash_token(raw_key)
        sample_api_key.revoked_at = datetime.now(timezone.utc)
        mock_api_key_repo.get_by_hash.return_value = (sample_api_key, sample_user)

        with pytest.raises(APIKeyRevokedError):
            await api_key_service.authenticate_key(raw_key)

    @pytest.mark.asyncio
    async def test_authenticate_expired_key_raises(
        self, api_key_service, mock_api_key_repo, sample_user, sample_api_key
    ):
        raw_key = "locus_live_expiredkey"
        sample_api_key.key_hash = hash_token(raw_key)
        sample_api_key.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        mock_api_key_repo.get_by_hash.return_value = (sample_api_key, sample_user)

        with pytest.raises(APIKeyExpiredError):
            await api_key_service.authenticate_key(raw_key)

    @pytest.mark.asyncio
    async def test_authenticate_inactive_user_raises(
        self, api_key_service, mock_api_key_repo, sample_user, sample_api_key
    ):
        raw_key = "locus_live_inactiveuserkey"
        sample_api_key.key_hash = hash_token(raw_key)
        sample_user.is_active = False
        mock_api_key_repo.get_by_hash.return_value = (sample_api_key, sample_user)

        with pytest.raises(UserInactiveError):
            await api_key_service.authenticate_key(raw_key)

    @pytest.mark.asyncio
    async def test_authenticate_unverified_user_allowed_five_requests_then_blocked_and_emailed(
        self, api_key_service, mock_api_key_repo, mock_user_repo, mock_email_provider, sample_user, sample_api_key
    ):
        raw_key = "locus_live_unverifiedkey"
        sample_api_key.key_hash = hash_token(raw_key)
        sample_user.email_verified_at = None
        sample_user.unverified_request_count = 0
        mock_api_key_repo.get_by_hash.return_value = (sample_api_key, sample_user)

        # Requests 1 to 5 should succeed
        for expected_count in range(1, 6):
            mock_user_repo.increment_unverified_request_count.return_value = expected_count
            key, user = await api_key_service.authenticate_key(raw_key)
            assert key.id == sample_api_key.id
            assert user.id == sample_user.id
            assert user.unverified_request_count == expected_count

        # At the end of the trial period (5th request), activation email was triggered
        assert len(mock_email_provider.sent_emails) >= 1
        assert mock_email_provider.sent_emails[-1]["to_email"] == sample_user.email

        # 6th request: user has hit max (5 requests), should be blocked with limit message
        sample_user.unverified_request_count = 5
        with pytest.raises(UnverifiedTrialLimitReachedError, match="maximum of 5 requests"):
            await api_key_service.authenticate_key(raw_key)

    @pytest.mark.asyncio
    async def test_authenticate_verified_user_not_limited(
        self, api_key_service, mock_api_key_repo, mock_user_repo, sample_user, sample_api_key
    ):
        raw_key = "locus_live_verifiedkey"
        sample_api_key.key_hash = hash_token(raw_key)
        sample_user.email_verified_at = datetime.now(timezone.utc)
        mock_api_key_repo.get_by_hash.return_value = (sample_api_key, sample_user)

        # Verified users can make any number of requests without trial increment
        for _ in range(10):
            key, user = await api_key_service.authenticate_key(raw_key)
            assert user.is_verified is True

        mock_user_repo.increment_unverified_request_count.assert_not_awaited()
