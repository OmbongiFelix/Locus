"""
Domain models for user accounts, verification tokens, and API keys.

Plain Python dataclasses representing core domain entities.
No FastAPI, Pydantic, or database/driver imports belong here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class User:
    """Internal domain representation of a user account."""

    id: UUID
    email: str
    password_hash: str
    is_active: bool
    email_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_verified(self) -> bool:
        """Check if user email has been verified."""
        return self.email_verified_at is not None


@dataclass
class EmailVerificationToken:
    """Internal domain representation of an email verification token."""

    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime

    @property
    def is_used(self) -> bool:
        """Check if token has already been consumed."""
        return self.used_at is not None

    def is_expired(self, now: datetime) -> bool:
        """Check if token is expired relative to given datetime."""
        return now > self.expires_at


@dataclass
class APIKey:
    """Internal domain representation of an API key."""

    id: UUID
    user_id: UUID
    name: str
    key_prefix: str
    key_hash: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None

    @property
    def is_revoked(self) -> bool:
        """Check if key has been explicitly revoked."""
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        """Check if key has expired."""
        if self.expires_at is None:
            return False
        return now > self.expires_at

    def is_valid(self, now: datetime) -> bool:
        """A key is valid if it is neither revoked nor expired."""
        return not self.is_revoked and not self.is_expired(now)
