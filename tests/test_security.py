"""
Tests for locus/security.py: password hashing, token generation, and HS256 JWT handling.
"""

from datetime import timedelta
import time
from uuid import uuid4

import pytest

from locus.security import (
    create_access_token,
    decode_access_token,
    generate_api_key,
    generate_verification_token,
    hash_password,
    hash_token,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_and_verify_success(self):
        password = "SuperSecretPassword123!"
        hashed = hash_password(password)
        assert hashed.startswith("$argon2id$")
        assert verify_password(password, hashed) is True

    def test_verify_wrong_password_fails(self):
        hashed = hash_password("CorrectPassword123")
        assert verify_password("WrongPassword123", hashed) is False

    def test_verify_malformed_hash_fails_safely(self):
        assert verify_password("password", "not-a-valid-argon2-hash") is False

    def test_different_hashes_for_same_password(self):
        pwd = "SamePassword123!"
        h1 = hash_password(pwd)
        h2 = hash_password(pwd)
        assert h1 != h2  # unique salts
        assert verify_password(pwd, h1) is True
        assert verify_password(pwd, h2) is True


class TestTokenHashingAndGeneration:
    def test_hash_token_deterministic(self):
        token = "random-token-12345"
        h1 = hash_token(token)
        h2 = hash_token(token)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex string

    def test_generate_verification_token(self):
        raw_token, token_hash = generate_verification_token()
        assert len(raw_token) > 20
        assert hash_token(raw_token) == token_hash

    def test_generate_api_key(self):
        raw_key, key_prefix, key_hash = generate_api_key(prefix="locus_live_")
        assert raw_key.startswith("locus_live_")
        assert key_prefix.startswith("locus_live_")
        assert len(key_prefix) == len("locus_live_") + 4
        assert hash_token(raw_key) == key_hash

    def test_api_keys_are_unique_and_high_entropy(self):
        keys = [generate_api_key() for _ in range(50)]
        raw_keys = {k[0] for k in keys}
        hashes = {k[2] for k in keys}
        assert len(raw_keys) == 50
        assert len(hashes) == 50


class TestJWTTokens:
    def test_create_and_decode_valid_token(self):
        user_id = str(uuid4())
        token = create_access_token(
            data={"sub": user_id, "email": "test@example.com"},
            expires_delta=timedelta(minutes=15),
            secret_key="test-secret-key",
        )
        payload = decode_access_token(token, secret_key="test-secret-key")
        assert payload is not None
        assert payload["sub"] == user_id
        assert payload["email"] == "test@example.com"
        assert "exp" in payload

    def test_expired_token_rejected(self):
        token = create_access_token(
            data={"sub": str(uuid4())},
            expires_delta=timedelta(seconds=-10),  # expired in past
            secret_key="test-secret-key",
        )
        payload = decode_access_token(token, secret_key="test-secret-key")
        assert payload is None

    def test_tampered_token_signature_rejected(self):
        token = create_access_token(
            data={"sub": str(uuid4())},
            secret_key="secret-1",
        )
        # Try to decode with a different secret
        payload = decode_access_token(token, secret_key="secret-2")
        assert payload is None

    def test_malformed_token_rejected(self):
        assert decode_access_token("not.a.token") is None
        assert decode_access_token("invalid") is None
