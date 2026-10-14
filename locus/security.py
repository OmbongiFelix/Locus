"""
Password hashing, token generation, API key management, and JWT handling.

Argon2id is used for human-chosen passwords.
SHA-256 is used for cryptographically random high-entropy tokens and API keys.
HMAC-SHA256 (HS256) is used for JWT access tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from locus.config import get_settings

# Argon2id password hasher with secure defaults
_password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


# Precomputed valid Argon2id hash for constant-time dummy verifications
DUMMY_PASSWORD_HASH = _password_hasher.hash("locus_dummy_constant_time_pass_123")


def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return _password_hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against an Argon2id hash."""
    try:
        return _password_hasher.verify(hashed_password, plain_password)
    except Exception:
        return False


def hash_token(token: str) -> str:
    """Compute a SHA-256 hex digest of a high-entropy secret token or API key.

    Used for uniformly random, high-entropy secrets (API keys, verification tokens).
    Never used for low-entropy human passwords.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "locus_live_") -> tuple[str, str, str]:
    """Generate a high-entropy API key.

    Returns:
        (raw_key, key_prefix, key_hash)
        raw_key: The plaintext key shown only once to the user (e.g. locus_live_...).
        key_prefix: Short prefix safe for display and identification (e.g. locus_live_ab12).
        key_hash: SHA-256 hash stored in the database.
    """
    # 32 bytes (256 bits) of cryptographic randomness in URL-safe base64 (~43 chars)
    random_part = secrets.token_urlsafe(32)
    raw_key = f"{prefix}{random_part}"
    # Prefix includes prefix identifier plus first 4 random chars, e.g. "locus_live_abcd"
    key_prefix = raw_key[: len(prefix) + 4]
    key_hash = hash_token(raw_key)
    return raw_key, key_prefix, key_hash


def generate_verification_token() -> tuple[str, str]:
    """Generate a cryptographically secure, single-use verification token.

    Returns:
        (token, token_hash)
        token: Plaintext token sent in verification email link.
        token_hash: SHA-256 hash stored in database.
    """
    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)
    return token, token_hash


# ── Base64URL and JWT (HS256) helpers ─────────────────────────────────


def _b64url_encode(data: bytes) -> str:
    """URL-safe Base64 encode without trailing padding."""
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    """URL-safe Base64 decode with automatic padding restoration."""
    padding = 4 - (len(s) % 4)
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s.encode("utf-8"))


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
    secret_key: str | None = None,
) -> str:
    """Create a signed HS256 JWT access token.

    Args:
        data: Payload claims (must include "sub").
        expires_delta: Optional custom lifetime.
        secret_key: Signing secret key (defaults to settings.SECRET_KEY).
    """
    settings = get_settings()
    secret = secret_key or getattr(settings, "SECRET_KEY", "insecure-dev-secret-key-change-me")
    now = int(time.time())

    if expires_delta is not None:
        exp = now + int(expires_delta.total_seconds())
    else:
        exp_minutes = getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 60)
        exp = now + int(timedelta(minutes=exp_minutes).total_seconds())

    payload = {
        **data,
        "iat": now,
        "exp": exp,
    }

    header = {"alg": "HS256", "typ": "JWT"}

    encoded_header = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")

    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    encoded_signature = _b64url_encode(signature)

    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def decode_access_token(
    token: str,
    secret_key: str | None = None,
) -> dict[str, Any] | None:
    """Decode and verify an HS256 JWT access token.

    Returns the payload dictionary if valid and unexpired; None otherwise.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None

    encoded_header, encoded_payload, encoded_signature = parts

    settings = get_settings()
    secret = secret_key or getattr(settings, "SECRET_KEY", "insecure-dev-secret-key-change-me")

    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()

    try:
        provided_sig = _b64url_decode(encoded_signature)
    except Exception:
        return None

    if not hmac.compare_digest(expected_sig, provided_sig):
        return None

    try:
        header_data = json.loads(_b64url_decode(encoded_header).decode("utf-8"))
        if header_data.get("alg") != "HS256":
            return None

        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except Exception:
        return None

    now = int(time.time())
    exp = payload.get("exp")
    if exp is not None and now > exp:
        return None

    return payload
