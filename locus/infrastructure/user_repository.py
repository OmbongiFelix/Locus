"""
Database repository for users and email verification tokens.

Follows the existing infrastructure pattern from boundary_repository.py,
using asyncpg with parameterized queries ($1, $2, ...) and get_connection().
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
import asyncpg

from locus.domain.models import EmailVerificationToken, User
from locus.infrastructure.db import get_connection


def _record_to_user(row: asyncpg.Record) -> User:
    """Map asyncpg Record to User domain dataclass."""
    user_id = row["id"]
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    return User(
        id=user_id,
        email=row["email"],
        password_hash=row["password_hash"],
        is_active=row["is_active"],
        email_verified_at=row["email_verified_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _record_to_token(row: asyncpg.Record) -> EmailVerificationToken:
    """Map asyncpg Record to EmailVerificationToken domain dataclass."""
    token_id = row["id"]
    if isinstance(token_id, str):
        token_id = UUID(token_id)
    user_id = row["user_id"]
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    return EmailVerificationToken(
        id=token_id,
        user_id=user_id,
        token_hash=row["token_hash"],
        expires_at=row["expires_at"],
        used_at=row["used_at"],
        created_at=row["created_at"],
    )


class UserRepository:
    """Repository handling SQL persistence for User and EmailVerificationToken."""

    async def create_user(self, email: str, password_hash: str) -> User:
        """Insert a new user record.

        Raises asyncpg.UniqueViolationError if email already exists.
        """
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (email, password_hash, is_active, email_verified_at)
                VALUES ($1, $2, true, NULL)
                RETURNING id, email, password_hash, is_active, email_verified_at, created_at, updated_at
                """,
                email.strip().lower(),
                password_hash,
            )
        return _record_to_user(row)

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Fetch user by primary key ID."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, email, password_hash, is_active, email_verified_at, created_at, updated_at
                  FROM users
                 WHERE id = $1
                """,
                user_id,
            )
        if row is None:
            return None
        return _record_to_user(row)

    async def get_by_email(self, email: str) -> User | None:
        """Fetch user by case-insensitive email address."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, email, password_hash, is_active, email_verified_at, created_at, updated_at
                  FROM users
                 WHERE LOWER(email) = LOWER($1)
                """,
                email.strip(),
            )
        if row is None:
            return None
        return _record_to_user(row)

    async def create_verification_token(
        self, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> EmailVerificationToken:
        """Persist a single-use verification token."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO email_verification_tokens (user_id, token_hash, expires_at)
                VALUES ($1, $2, $3)
                RETURNING id, user_id, token_hash, expires_at, used_at, created_at
                """,
                user_id,
                token_hash,
                expires_at,
            )
        return _record_to_token(row)

    async def get_verification_token(
        self, token_hash: str
    ) -> EmailVerificationToken | None:
        """Find a verification token record by token_hash."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, token_hash, expires_at, used_at, created_at
                  FROM email_verification_tokens
                 WHERE token_hash = $1
                """,
                token_hash,
            )
        if row is None:
            return None
        return _record_to_token(row)

    async def verify_email_atomically(
        self, token_id: UUID, user_id: UUID, verified_at: datetime
    ) -> bool:
        """Atomically mark token as used and user account as verified inside a transaction.

        Returns True on success, or False if the token was already consumed.
        """
        async with get_connection() as conn:
            async with conn.transaction():
                # Attempt to consume token (single-use semantics enforced at DB level)
                update_token = await conn.execute(
                    """
                    UPDATE email_verification_tokens
                       SET used_at = $1
                     WHERE id = $2
                       AND used_at IS NULL
                    """,
                    verified_at,
                    token_id,
                )
                if update_token != "UPDATE 1":
                    return False

                # Mark user account verified
                await conn.execute(
                    """
                    UPDATE users
                       SET email_verified_at = $1,
                           updated_at = $1
                     WHERE id = $2
                    """,
                    verified_at,
                    user_id,
                )
                return True

    async def invalidate_pending_tokens(self, user_id: UUID) -> None:
        """Mark any outstanding unused tokens for user as expired/used (e.g. before resending)."""
        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE email_verification_tokens
                   SET used_at = now()
                 WHERE user_id = $1
                   AND used_at IS NULL
                """,
                user_id,
            )
