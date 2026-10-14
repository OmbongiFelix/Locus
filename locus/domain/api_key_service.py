"""
Domain service governing API key lifecycle, issuance, listing, revocation, and authentication.

Independent of web framework and database drivers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from locus.domain.models import APIKey, User
from locus.infrastructure.api_key_repository import APIKeyRepository
from locus.security import generate_api_key, hash_token


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


class InvalidKeyNameError(APIKeyError):
    """Raised when an invalid key name is provided."""


class APIKeyService:
    """Orchestrates API key generation, listing, revocation, and authentication."""

    def __init__(self, repository: APIKeyRepository) -> None:
        self._repository = repository

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
        6. User email is verified

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
            raise UserUnverifiedError("User account email is unverified.")

        return api_key, user
