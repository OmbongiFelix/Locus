"""
Database repository for API key management and authentication queries.

Follows the existing infrastructure pattern from boundary_repository.py,
using asyncpg with parameterized queries ($1, $2, ...) and get_connection().
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
import asyncpg

from locus.domain.models import APIKey, User
from locus.infrastructure.db import get_connection
from locus.infrastructure.user_repository import _record_to_user


def _record_to_api_key(row: asyncpg.Record) -> APIKey:
    """Map asyncpg Record to APIKey domain dataclass."""
    key_id = row["id"]
    if isinstance(key_id, str):
        key_id = UUID(key_id)
    user_id = row["user_id"]
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    return APIKey(
        id=key_id,
        user_id=user_id,
        name=row["name"],
        key_prefix=row["key_prefix"],
        key_hash=row["key_hash"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
    )


class APIKeyRepository:
    """Repository handling SQL persistence for APIKey records."""

    async def create_api_key(
        self,
        user_id: UUID,
        name: str,
        key_prefix: str,
        key_hash: str,
        expires_at: datetime | None = None,
    ) -> APIKey:
        """Insert a newly generated API key record.

        Raises asyncpg.UniqueViolationError if key_hash collides.
        """
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO api_keys (user_id, name, key_prefix, key_hash, expires_at)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, user_id, name, key_prefix, key_hash, created_at, expires_at, revoked_at
                """,
                user_id,
                name.strip(),
                key_prefix,
                key_hash,
                expires_at,
            )
        return _record_to_api_key(row)

    async def list_by_user_id(self, user_id: UUID) -> list[APIKey]:
        """Fetch all API keys belonging to a user, ordered by creation date desc."""
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, name, key_prefix, key_hash, created_at, expires_at, revoked_at
                  FROM api_keys
                 WHERE user_id = $1
                 ORDER BY created_at DESC
                """,
                user_id,
            )
        return [_record_to_api_key(r) for r in rows]

    async def get_by_id_and_user_id(
        self, key_id: UUID, user_id: UUID
    ) -> APIKey | None:
        """Fetch an API key only if it belongs to the given user (prevents guessing/enumeration)."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, name, key_prefix, key_hash, created_at, expires_at, revoked_at
                  FROM api_keys
                 WHERE id = $1
                   AND user_id = $2
                """,
                key_id,
                user_id,
            )
        if row is None:
            return None
        return _record_to_api_key(row)

    async def get_by_hash(self, key_hash: str) -> tuple[APIKey, User] | None:
        """Find an API key and joined user by key_hash for request authentication."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT k.id AS key_id, k.user_id, k.name, k.key_prefix, k.key_hash,
                       k.created_at AS key_created_at, k.expires_at, k.revoked_at,
                       u.id AS user_pk, u.email, u.password_hash, u.is_active,
                       u.email_verified_at, u.unverified_request_count, u.created_at AS user_created_at, u.updated_at
                  FROM api_keys k
                  JOIN users u ON k.user_id = u.id
                 WHERE k.key_hash = $1
                """,
                key_hash,
            )
        if row is None:
            return None

        key_id = row["key_id"]
        if isinstance(key_id, str):
            key_id = UUID(key_id)
        user_id = row["user_id"]
        if isinstance(user_id, str):
            user_id = UUID(user_id)

        api_key = APIKey(
            id=key_id,
            user_id=user_id,
            name=row["name"],
            key_prefix=row["key_prefix"],
            key_hash=row["key_hash"],
            created_at=row["key_created_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        )

        try:
            unverified_count = int(row["unverified_request_count"])
        except (KeyError, IndexError, TypeError):
            unverified_count = 0

        user = User(
            id=user_id,
            email=row["email"],
            password_hash=row["password_hash"],
            is_active=row["is_active"],
            email_verified_at=row["email_verified_at"],
            created_at=row["user_created_at"],
            updated_at=row["updated_at"],
            unverified_request_count=unverified_count,
        )

        return api_key, user

    async def revoke_api_key(
        self, key_id: UUID, user_id: UUID, revoked_at: datetime
    ) -> APIKey | None:
        """Revoke an API key belonging to the user.

        If already revoked, preserves original revoked_at and returns the record (idempotent).
        If the key does not exist or belongs to another user, returns None.
        """
        async with get_connection() as conn:
            # First check if the key belongs to this user
            existing = await self.get_by_id_and_user_id(key_id, user_id)
            if existing is None:
                return None
            if existing.is_revoked:
                return existing

            row = await conn.fetchrow(
                """
                UPDATE api_keys
                   SET revoked_at = $1
                 WHERE id = $2
                   AND user_id = $3
                   AND revoked_at IS NULL
                RETURNING id, user_id, name, key_prefix, key_hash, created_at, expires_at, revoked_at
                """,
                revoked_at,
                key_id,
                user_id,
            )
        if row is None:
            return existing
        return _record_to_api_key(row)
