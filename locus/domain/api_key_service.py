"""
Domain service governing API key lifecycle, issuance, listing, revocation, and authentication.

Independent of web framework and database drivers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from locus.config import get_settings
from locus.domain.models import APIKey, User
from locus.infrastructure.api_key_repository import APIKeyRepository
from locus.infrastructure.email_service import EmailProvider, get_email_provider
from locus.infrastructure.user_repository import UserRepository
from locus.security import generate_api_key, generate_verification_token, hash_token

# In-memory cooldown tracker for trial expiration emails to prevent spamming
_last_activation_email: dict[UUID, float] = {}


class APIKeyError(Exception):
    """Base class for API key errors."""


class InvalidAPIKeyError(APIKeyError):
    """Raised when an API key is unknown, malformed, or has invalid signature."""


class APIKeyNotFoundError(APIKeyError):
    """Raised when looking up an API key that does not exist or belongs to another user."""


class APIKeyRevokedError(APIKeyError):
    """Raised when attempting to authenticate with a revoked API key."""


class APIKeyExpiredError(APIKeyError):
    """Raised when attempting to authenticate with an expired API key."""


class UserInactiveError(APIKeyError):
    """Raised when the owner of an API key is inactive/disabled."""


class UserUnverifiedError(APIKeyError):
    """Raised when the owner of an API key is unverified."""


class UnverifiedTrialLimitReachedError(UserUnverifiedError):
    """Raised when an unverified user has reached their maximum allowed trial requests."""


class InvalidKeyNameError(APIKeyError):
    """Raised when an invalid key name is provided."""


class APIKeyService:
    """Orchestrates API key generation, listing, revocation, and authentication."""

    def __init__(
        self,
        repository: APIKeyRepository,
        user_repository: UserRepository | None = None,
        email_provider: EmailProvider | None = None,
    ) -> None:
        self._repository = repository
        self._user_repository = user_repository
        self._email_provider = email_provider

    async def _send_activation_email(self, user: User) -> None:
        """Send an account activation email when the trial limit is reached."""
        if self._user_repository is None:
            return

        now_ts = datetime.now(timezone.utc).timestamp()
        last_sent = _last_activation_email.get(user.id, 0.0)
        # Avoid duplicate email spam within 60 seconds
        if now_ts - last_sent < 60.0:
            return
        _last_activation_email[user.id] = now_ts

        provider = self._email_provider or get_email_provider()

        # Invalidate any pending unused tokens
        await self._user_repository.invalidate_pending_tokens(user.id)

        # Issue fresh token
        raw_token, token_hash = generate_verification_token()
        settings = get_settings()
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=getattr(settings, "VERIFICATION_TOKEN_EXPIRE_HOURS", 24)
        )

        await self._user_repository.create_verification_token(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        await provider.send_verification_email(user.email, raw_token)

    async def create_key(
        self,
        user_id: UUID,
        name: str,
        expires_in_days: int | None = None,
    ) -> tuple[APIKey, str]:
        """Generate a new high-entropy API key.

        Returns:
            (APIKey, raw_key)
            The raw_key is returned ONLY once upon creation and never stored.
        """
        cleaned_name = name.strip()
        if not cleaned_name:
            raise InvalidKeyNameError("API key name must not be empty.")
        if len(cleaned_name) > 100:
            raise InvalidKeyNameError("API key name must not exceed 100 characters.")

        expires_at: datetime | None = None
        if expires_in_days is not None:
            if expires_in_days <= 0:
                raise ValueError("expires_in_days must be a positive integer.")
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

        raw_key, key_prefix, key_hash = generate_api_key(prefix="locus_live_")

        record = await self._repository.create_api_key(
            user_id=user_id,
            name=cleaned_name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            expires_at=expires_at,
        )

        return record, raw_key

    async def list_keys(self, user_id: UUID) -> list[APIKey]:
        """List all API keys belonging to a user (safe metadata only)."""
        return await self._repository.list_by_user_id(user_id)

    async def get_key(self, key_id: UUID, user_id: UUID) -> APIKey:
        """Fetch metadata for a single key, scoped strictly to the authenticated user."""
        key = await self._repository.get_by_id_and_user_id(key_id, user_id)
        if key is None:
            raise APIKeyNotFoundError("API key not found.")
        return key

    async def revoke_key(self, key_id: UUID, user_id: UUID) -> APIKey:
        """Revoke an API key belonging to the user."""
        revoked = await self._repository.revoke_api_key(
            key_id=key_id,
            user_id=user_id,
            revoked_at=datetime.now(timezone.utc),
        )
        if revoked is None:
            raise APIKeyNotFoundError("API key not found.")
        return revoked

    async def authenticate_key(self, raw_key: str) -> tuple[APIKey, User]:
        """Validate an API key and resolve its active user.

        Verifies:
        1. Non-empty string
        2. Key exists in database
        3. Key is not revoked
        4. Key is not expired
        5. User exists and is active
        6. Unverified accounts are limited to MAX_UNVERIFIED_REQUESTS (default 5).
           Once exceeded, raises UnverifiedTrialLimitReachedError and dispatches activation email.

        Returns:
            (APIKey, User)
        """
        token = raw_key.strip()
        if not token:
            raise InvalidAPIKeyError("API key is required.")

        key_hash = hash_token(token)
        result = await self._repository.get_by_hash(key_hash)
        if result is None:
            raise InvalidAPIKeyError("Invalid API key.")

        api_key, user = result
        now = datetime.now(timezone.utc)

        if api_key.is_revoked:
            raise APIKeyRevokedError("API key has been revoked.")

        if api_key.is_expired(now):
            raise APIKeyExpiredError("API key has expired.")

        if not user.is_active:
            raise UserInactiveError("User account is inactive.")

        if not user.is_verified:
            settings = get_settings()
            max_trial = getattr(settings, "MAX_UNVERIFIED_REQUESTS", 5)

            if user.unverified_request_count >= max_trial:
                # Deliver activation email when the limit message is returned
                await self._send_activation_email(user)
                raise UnverifiedTrialLimitReachedError(
                    f"You have reached the maximum of {max_trial} requests for unverified accounts. "
                    "Please activate your account by verifying your email to continue using the API."
                )

            # Increment trial request count
            if self._user_repository is not None:
                new_count = await self._user_repository.increment_unverified_request_count(user.id)
                user.unverified_request_count = new_count
            else:
                user.unverified_request_count += 1

            # Dispatch activation email at the end of the trial period
            if user.unverified_request_count >= max_trial:
                await self._send_activation_email(user)

        return api_key, user
