"""
Unit tests for locus/domain/auth_service.py.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

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
from locus.domain.models import EmailVerificationToken, User
from locus.infrastructure.email_service import MockEmailProvider
from locus.infrastructure.user_repository import UserRepository
from locus.security import hash_password, hash_token


@pytest.fixture
def mock_user_repo():
    return AsyncMock(spec=UserRepository)


@pytest.fixture
def mock_email_provider():
    return MockEmailProvider()


@pytest.fixture
def auth_service(mock_user_repo, mock_email_provider):
    return AuthService(repository=mock_user_repo, email_provider=mock_email_provider)


@pytest.fixture
def sample_user():
    return User(
        id=uuid4(),
        email="test@example.com",
        password_hash=hash_password("ValidPassword123!"),
        is_active=True,
        email_verified_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class TestUserRegistration:
    @pytest.mark.asyncio
    async def test_successful_registration(
        self, auth_service, mock_user_repo, mock_email_provider, sample_user
    ):
        mock_user_repo.get_by_email.return_value = None
        mock_user_repo.create_user.return_value = sample_user
        mock_user_repo.create_verification_token.return_value = AsyncMock()

        user, raw_token = await auth_service.register("Test@Example.COM", "ValidPassword123!")

        assert user.email == "test@example.com"
        assert user.email_verified_at is None
        assert not user.is_verified
        assert len(raw_token) > 20
        mock_user_repo.create_user.assert_awaited_once()
        mock_user_repo.create_verification_token.assert_awaited_once()
        assert len(mock_email_provider.sent_emails) == 1
        assert mock_email_provider.sent_emails[0]["to_email"] == "test@example.com"

    @pytest.mark.asyncio
    async def test_duplicate_registration_raises_conflict(
        self, auth_service, mock_user_repo, sample_user
    ):
        mock_user_repo.get_by_email.return_value = sample_user

        with pytest.raises(EmailAlreadyExistsError):
            await auth_service.register("test@example.com", "ValidPassword123!")

        mock_user_repo.create_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_short_password_rejected(self, auth_service):
        with pytest.raises(WeakPasswordError):
            await auth_service.register("user@example.com", "short")

    @pytest.mark.asyncio
    async def test_invalid_email_format_rejected(self, auth_service):
        with pytest.raises(InvalidEmailError):
            await auth_service.register("not-an-email", "ValidPassword123!")


class TestEmailVerification:
    @pytest.mark.asyncio
    async def test_successful_verification(
        self, auth_service, mock_user_repo, sample_user
    ):
        raw_token = "valid-raw-token"
        token_hash = hash_token(raw_token)
        token_record = EmailVerificationToken(
            id=uuid4(),
            user_id=sample_user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            used_at=None,
            created_at=datetime.now(timezone.utc),
        )
        verified_user = User(
            id=sample_user.id,
            email=sample_user.email,
            password_hash=sample_user.password_hash,
            is_active=True,
            email_verified_at=datetime.now(timezone.utc),
            created_at=sample_user.created_at,
            updated_at=datetime.now(timezone.utc),
        )

        mock_user_repo.get_verification_token.return_value = token_record
        mock_user_repo.verify_email_atomically.return_value = True
        mock_user_repo.get_by_id.return_value = verified_user

        user = await auth_service.verify_email(raw_token)
        assert user.is_verified is True
        mock_user_repo.verify_email_atomically.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_token_rejected(self, auth_service, mock_user_repo):
        mock_user_repo.get_verification_token.return_value = None

        with pytest.raises(InvalidTokenError):
            await auth_service.verify_email("unknown-token")

    @pytest.mark.asyncio
    async def test_already_used_token_rejected(
        self, auth_service, mock_user_repo, sample_user
    ):
        raw_token = "used-token"
        token_record = EmailVerificationToken(
            id=uuid4(),
            user_id=sample_user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            used_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        mock_user_repo.get_verification_token.return_value = token_record

        with pytest.raises(TokenAlreadyUsedError):
            await auth_service.verify_email(raw_token)

    @pytest.mark.asyncio
    async def test_expired_token_rejected(
        self, auth_service, mock_user_repo, sample_user
    ):
        raw_token = "expired-token"
        token_record = EmailVerificationToken(
            id=uuid4(),
            user_id=sample_user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),  # expired
            used_at=None,
            created_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        mock_user_repo.get_verification_token.return_value = token_record

        with pytest.raises(TokenExpiredError):
            await auth_service.verify_email(raw_token)


class TestResendVerification:
    @pytest.mark.asyncio
    async def test_resend_for_unverified_user(
        self, auth_service, mock_user_repo, mock_email_provider, sample_user
    ):
        mock_user_repo.get_by_email.return_value = sample_user
        mock_user_repo.invalidate_pending_tokens.return_value = None
        mock_user_repo.create_verification_token.return_value = AsyncMock()

        success = await auth_service.resend_verification("test@example.com")
        assert success is True
        mock_user_repo.invalidate_pending_tokens.assert_awaited_once_with(sample_user.id)
        assert len(mock_email_provider.sent_emails) == 1

    @pytest.mark.asyncio
    async def test_resend_for_already_verified_user_returns_false_silently(
        self, auth_service, mock_user_repo, mock_email_provider, sample_user
    ):
        sample_user.email_verified_at = datetime.now(timezone.utc)
        mock_user_repo.get_by_email.return_value = sample_user

        success = await auth_service.resend_verification("test@example.com")
        assert success is False
        assert len(mock_email_provider.sent_emails) == 0

    @pytest.mark.asyncio
    async def test_resend_for_nonexistent_user_returns_false_silently(
        self, auth_service, mock_user_repo, mock_email_provider
    ):
        mock_user_repo.get_by_email.return_value = None

        success = await auth_service.resend_verification("unknown@example.com")
        assert success is False
        assert len(mock_email_provider.sent_emails) == 0


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_authenticate_success(self, auth_service, mock_user_repo, sample_user):
        sample_user.email_verified_at = datetime.now(timezone.utc)
        mock_user_repo.get_by_email.return_value = sample_user

        user = await auth_service.authenticate("test@example.com", "ValidPassword123!")
        assert user.id == sample_user.id

    @pytest.mark.asyncio
    async def test_authenticate_wrong_password(
        self, auth_service, mock_user_repo, sample_user
    ):
        sample_user.email_verified_at = datetime.now(timezone.utc)
        mock_user_repo.get_by_email.return_value = sample_user

        with pytest.raises(InvalidCredentialsError):
            await auth_service.authenticate("test@example.com", "WrongPassword!")

    @pytest.mark.asyncio
    async def test_authenticate_unknown_user(self, auth_service, mock_user_repo):
        mock_user_repo.get_by_email.return_value = None

        with pytest.raises(InvalidCredentialsError):
            await auth_service.authenticate("nobody@example.com", "AnyPassword123!")

    @pytest.mark.asyncio
    async def test_authenticate_unverified_account_rejected(
        self, auth_service, mock_user_repo, sample_user
    ):
        sample_user.email_verified_at = None
        mock_user_repo.get_by_email.return_value = sample_user

        with pytest.raises(AccountUnverifiedError):
            await auth_service.authenticate("test@example.com", "ValidPassword123!")

    @pytest.mark.asyncio
    async def test_authenticate_inactive_account_rejected(
        self, auth_service, mock_user_repo, sample_user
    ):
        sample_user.email_verified_at = datetime.now(timezone.utc)
        sample_user.is_active = False
        mock_user_repo.get_by_email.return_value = sample_user

        with pytest.raises(AccountInactiveError):
            await auth_service.authenticate("test@example.com", "ValidPassword123!")
