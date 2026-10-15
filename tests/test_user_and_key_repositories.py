"""
Tests for UserRepository and APIKeyRepository (unit record mapping and integration queries).
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4
import os

import pytest
import pytest_asyncio
import asyncpg

from locus.domain.models import APIKey, EmailVerificationToken, User
from locus.infrastructure.user_repository import (
    UserRepository,
    _record_to_token,
    _record_to_user,
)
from locus.infrastructure.api_key_repository import (
    APIKeyRepository,
    _record_to_api_key,
)


class TestRecordMapping:
    def test_record_to_user_mapping(self):
        uid = uuid4()
        now = datetime.now(timezone.utc)
        record = {
            "id": uid,
            "email": "user@example.com",
            "password_hash": "$argon2id$fakehash",
            "is_active": True,
            "email_verified_at": now,
            "created_at": now,
            "updated_at": now,
        }
        user = _record_to_user(record)
        assert isinstance(user, User)
        assert user.id == uid
        assert user.email == "user@example.com"
        assert user.is_verified is True

    def test_record_to_token_mapping(self):
        tid = uuid4()
        uid = uuid4()
        now = datetime.now(timezone.utc)
        record = {
            "id": tid,
            "user_id": uid,
            "token_hash": "sha256hash",
            "expires_at": now,
            "used_at": None,
            "created_at": now,
        }
        token = _record_to_token(record)
        assert isinstance(token, EmailVerificationToken)
        assert token.id == tid
        assert token.user_id == uid
        assert token.is_used is False

    def test_record_to_api_key_mapping(self):
        kid = uuid4()
        uid = uuid4()
        now = datetime.now(timezone.utc)
        record = {
            "id": kid,
            "user_id": uid,
            "name": "App Key",
            "key_prefix": "locus_live_abcd",
            "key_hash": "sha256hash",
            "created_at": now,
            "expires_at": None,
            "revoked_at": None,
        }
        key = _record_to_api_key(record)
        assert isinstance(key, APIKey)
        assert key.id == kid
        assert key.user_id == uid
        assert key.name == "App Key"
        assert key.is_revoked is False


# ── Integration tests (require real database via docker-compose.test.yml) ────


TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://test_user:test_password@localhost:5433/test_db",
)


@pytest.mark.integration
class TestRepositoryDatabaseIntegration:
    """Integration tests running against real PostgreSQL/PostGIS."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self):
        pool = await asyncpg.create_pool(dsn=TEST_DB_URL)
        async with pool.acquire() as conn:
            # Run migrations
            with open("migrations/002_users.sql") as f:
                await conn.execute(f.read())
            with open("migrations/003_api_keys.sql") as f:
                await conn.execute(f.read())
        yield
        await pool.close()

    @pytest.mark.asyncio
    async def test_user_and_api_key_lifecycle_db(self):
        user_repo = UserRepository()
        key_repo = APIKeyRepository()

        # Create user
        user = await user_repo.create_user("integration@example.com", "hash123")
        assert user.email == "integration@example.com"

        # Create key
        key = await key_repo.create_api_key(
            user_id=user.id,
            name="Integration Key",
            key_prefix="locus_live_intg",
            key_hash="unique_hash_123",
        )
        assert key.name == "Integration Key"

        # Lookup by hash
        found = await key_repo.get_by_hash("unique_hash_123")
        assert found is not None
        retrieved_key, retrieved_user = found
        assert retrieved_key.id == key.id
        assert retrieved_user.id == user.id

        # Revoke
        revoked = await key_repo.revoke_api_key(key.id, user.id, datetime.now(timezone.utc))
        assert revoked is not None
        assert revoked.is_revoked is True
