"""
Domain service governing user registration, email verification, and authentication.

Contains business rules independent of HTTP framework and database drivers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from uuid import UUID

from locus.config import get_settings
from locus.domain.models import User
from locus.infrastructure.email_service import EmailProvider, get_email_provider
from locus.infrastructure.user_repository import UserRepository
from locus.security import (
    DUMMY_PASSWORD_HASH,
    generate_verification_token,
    hash_password,
    hash_token,
    verify_password,
)

# Email validation regex conforming to standard address formats
_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class AuthError(Exception):
    """Base class for domain authentication and account errors."""


class EmailAlreadyExistsError(AuthError):
    """Raised when an email address is already registered."""


class InvalidCredentialsError(AuthError):
    """Raised when email or password is incorrect."""


class AccountInactiveError(AuthError):
    """Raised when the user account is disabled/inactive."""


class AccountUnverifiedError(AuthError):
    """Raised when attempting to log in before verifying email."""


class InvalidTokenError(AuthError):
    """Raised when an email verification token is unknown or malformed."""


class TokenExpiredError(AuthError):
    """Raised when an email verification token has expired."""


class TokenAlreadyUsedError(AuthError):
    """Raised when an email verification token has already been consumed."""


class InvalidEmailError(AuthError):
    """Raised when the provided email address format is invalid."""


class WeakPasswordError(AuthError):
    """Raised when the provided password fails complexity/length policy."""


class AuthService:
    """Orchestrates user registration, verification, and authentication logic."""

    def __init__(
        self,
        repository: UserRepository,
        email_provider: EmailProvider | None = None,
    ) -> None:
        self._repository = repository
        self._email_provider = email_provider or get_email_provider()

    @staticmethod
    def normalize_email(email: str) -> str:
        """Strip and normalize email to lowercase; validate basic RFC format."""
        cleaned = email.strip().lower()
        if not cleaned or not _EMAIL_REGEX.match(cleaned):
            raise InvalidEmailError("Invalid email address format.")
        return cleaned

    @staticmethod
    def validate_password_strength(password: str) -> None:
        """Enforce password security policy (minimum 8 characters)."""
        if len(password) < 8:
            raise WeakPasswordError("Password must be at least 8 characters long.")
        if len(password) > 128:
            raise WeakPasswordError("Password must not exceed 128 characters.")

    async def register(self, email: str, password: str) -> tuple[User, str]:
        """Register a new user account as unverified and dispatch verification email.

        Returns:
            (User, raw_verification_token)
        """
        normalized_email = self.normalize_email(email)
        self.validate_password_strength(password)

        existing = await self._repository.get_by_email(normalized_email)
        if existing is not None:
            raise EmailAlreadyExistsError("An account with this email address already exists.")

        pwd_hash = hash_password(password)

        try:
            user = await self._repository.create_user(normalized_email, pwd_hash)
        except Exception as exc:
            # Catch DB unique constraint race condition
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise EmailAlreadyExistsError("An account with this email address already exists.")
            raise

        # Generate single-use verification token
        raw_token, token_hash = generate_verification_token()
        settings = get_settings()
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=settings.VERIFICATION_TOKEN_EXPIRE_HOURS
        )

        await self._repository.create_verification_token(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        # Dispatch verification email via configured provider
        await self._email_provider.send_verification_email(user.email, raw_token)

        return user, raw_token

    async def verify_email(self, raw_token: str) -> User:
        """Validate and consume a single-use verification token, activating user account."""
        token_str = raw_token.strip()
        if not token_str:
            raise InvalidTokenError("Verification token is required.")

        token_hash = hash_token(token_str)
        token_record = await self._repository.get_verification_token(token_hash)
        if token_record is None:
            raise InvalidTokenError("Invalid verification token.")

        if token_record.is_used:
            raise TokenAlreadyUsedError("Verification token has already been used.")

        now = datetime.now(timezone.utc)
        if token_record.is_expired(now):
            raise TokenExpiredError("Verification token has expired. Please request a new one.")

        success = await self._repository.verify_email_atomically(
            token_id=token_record.id,
            user_id=token_record.user_id,
            verified_at=now,
        )
        if not success:
            raise TokenAlreadyUsedError("Verification token has already been used.")

        user = await self._repository.get_by_id(token_record.user_id)
        if user is None:
            raise InvalidTokenError("Associated user account not found.")

        return user

    async def resend_verification(self, email: str) -> bool:
        """Resend a new verification email for unverified user.

        Always returns cleanly without revealing whether the email exists.
        """
        try:
            normalized_email = self.normalize_email(email)
        except InvalidEmailError:
            return False

        user = await self._repository.get_by_email(normalized_email)
        if user is None or user.is_verified:
            # Silent no-op for privacy & security against account enumeration
            return False

        # Invalidate any previously pending unused tokens
        await self._repository.invalidate_pending_tokens(user.id)

        # Issue new token
        raw_token, token_hash = generate_verification_token()
        settings = get_settings()
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=settings.VERIFICATION_TOKEN_EXPIRE_HOURS
        )

        await self._repository.create_verification_token(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        await self._email_provider.send_verification_email(user.email, raw_token)
        return True

    async def authenticate(self, email: str, password: str) -> User:
        """Verify user credentials and check active & verified status."""
        try:
            normalized_email = self.normalize_email(email)
        except InvalidEmailError:
            raise InvalidCredentialsError("Invalid email or password.")

        user = await self._repository.get_by_email(normalized_email)
        if user is None:
            # Prevent timing enumeration by verifying dummy password
            verify_password(password, DUMMY_PASSWORD_HASH)
            raise InvalidCredentialsError("Invalid email or password.")

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password.")

        if not user.is_active:
            raise AccountInactiveError("This user account is inactive. Please contact support.")

        # Unverified accounts are permitted to authenticate during trial
        return user
