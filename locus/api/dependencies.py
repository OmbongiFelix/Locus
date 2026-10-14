"""
Request-scoped FastAPI dependencies: service construction, auth, and
rate limiting.

Centralizing these here means routes stay declarative - each route just
lists which dependencies it needs.
"""

from __future__ import annotations

import time
from collections import defaultdict
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request

from locus.config import get_settings
from locus.domain.api_key_service import (
    APIKeyExpiredError,
    APIKeyRevokedError,
    APIKeyService,
    InvalidAPIKeyError,
    UserInactiveError,
    UserUnverifiedError,
)
from locus.domain.auth_service import AuthService
from locus.domain.geocode_service import GeocodeService
from locus.domain.models import User
from locus.infrastructure.api_key_repository import APIKeyRepository
from locus.infrastructure.boundary_repository import BoundaryRepository
from locus.infrastructure.cache import GeocodeCache
from locus.infrastructure.user_repository import UserRepository
from locus.security import decode_access_token

# In-memory per-key rate-limit state for geocoding API keys
_rate_limit_state: dict[str, list[float]] = defaultdict(list)

# In-memory rate-limit state for authentication endpoints (keyed by client IP)
_auth_rate_limit_state: dict[str, list[float]] = defaultdict(list)


# ── Service & Repository factories ───────────────────────────────────


def get_geocode_service() -> GeocodeService:
    """Construct a GeocodeService wired with its repository and cache.

    Reuses the single DB pool and Redis client that were opened at startup.
    """
    return GeocodeService(
        repository=BoundaryRepository(),
        cache=GeocodeCache(),
    )


def get_user_repository() -> UserRepository:
    """Return UserRepository instance."""
    return UserRepository()


def get_api_key_repository() -> APIKeyRepository:
    """Return APIKeyRepository instance."""
    return APIKeyRepository()


def get_auth_service(
    user_repo: UserRepository = Depends(get_user_repository),
) -> AuthService:
    """Return AuthService wired with UserRepository."""
    return AuthService(repository=user_repo)


def get_api_key_service(
    key_repo: APIKeyRepository = Depends(get_api_key_repository),
) -> APIKeyService:
    """Return APIKeyService wired with APIKeyRepository."""
    return APIKeyService(repository=key_repo)


# ── Legacy API Key & Rate Limiting ───────────────────────────────────


async def verify_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> str:
    """Validate the API key from the ``X-API-Key`` header.

    If ``settings.API_KEYS`` is empty, all keys are accepted (dev mode).
    Preserved for backward compatibility with existing tests and clients.
    """
    settings = get_settings()
    valid_keys = [k.strip() for k in settings.API_KEYS.split(",") if k.strip()]
    if not valid_keys:
        # No keys configured — open access (development only)
        return x_api_key
    if x_api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key


async def rate_limiter(
    api_key: str = Depends(verify_api_key),
) -> None:
    """Enforce ``settings.RATE_LIMIT_PER_MINUTE`` per API key.

    Uses a simple sliding-window counter. Raises 429 with a
    ``Retry-After`` header when the limit is exceeded.
    """
    settings = get_settings()
    now = time.time()
    window_start = now - 60.0

    # Prune expired timestamps
    timestamps = _rate_limit_state[api_key]
    _rate_limit_state[api_key] = [ts for ts in timestamps if ts > window_start]

    if len(_rate_limit_state[api_key]) >= settings.RATE_LIMIT_PER_MINUTE:
        oldest = min(_rate_limit_state[api_key])
        retry_after = int(60 - (now - oldest)) + 1
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    _rate_limit_state[api_key].append(now)


async def auth_rate_limiter(request: Request) -> None:
    """Rate limit authentication endpoints (register, login, resend-verification) to mitigate brute force.

    Enforces ``settings.AUTH_RATE_LIMIT_PER_MINUTE`` per client IP.
    """
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_start = now - 60.0

    timestamps = _auth_rate_limit_state[client_ip]
    _auth_rate_limit_state[client_ip] = [ts for ts in timestamps if ts > window_start]

    limit = getattr(settings, "AUTH_RATE_LIMIT_PER_MINUTE", 10)
    if len(_auth_rate_limit_state[client_ip]) >= limit:
        oldest = min(_auth_rate_limit_state[client_ip])
        retry_after = int(60 - (now - oldest)) + 1
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limit_exceeded", "message": "Too many requests. Please try again later."},
            headers={"Retry-After": str(retry_after)},
        )

    _auth_rate_limit_state[client_ip].append(now)


# ── User Session / JWT Authentication ─────────────────────────────────


async def get_current_user(
    authorization: str | None = Header(None, alias="Authorization"),
    user_repo: UserRepository = Depends(get_user_repository),
) -> User:
    """Authenticate request using a user access token (Bearer <jwt>).

    Required for account management operations (e.g. creating/listing/revoking API keys).
    Never accepts API keys as identity tokens for credentials management.
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Missing Authorization header"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Invalid Authorization header format. Expected 'Bearer <token>'"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Invalid or expired access token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Invalid token subject claim"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Malformed user identifier in token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "User account no longer exists"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail={"error": "account_inactive", "message": "User account is disabled"},
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=403,
            detail={"error": "account_unverified", "message": "User email address is not verified"},
        )

    return user


# ── API Key Authentication for Protected Endpoints ───────────────────


async def get_current_user_from_api_key(
    authorization: str | None = Header(None, alias="Authorization"),
    key_service: APIKeyService = Depends(get_api_key_service),
) -> User:
    """Authenticate requests to protected API endpoints using user-generated API keys.

    Enforces:
    1. Read key from Authorization: Bearer <key>
    2. Reject missing or malformed credentials (401)
    3. Hash key and lookup database record
    4. Verify key exists, is not revoked, not expired (401)
    5. Confirm associated user exists and is active (403)
    6. Confirm user email is verified (403)
    7. Return authenticated User dataclass
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Missing Authorization header"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Invalid Authorization header format. Expected 'Bearer <api_key>'"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_key = parts[1]

    try:
        _api_key, user = await key_service.authenticate_key(raw_key)
        return user
    except InvalidAPIKeyError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_api_key", "message": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )
    except APIKeyRevokedError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "api_key_revoked", "message": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )
    except APIKeyExpiredError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "api_key_expired", "message": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )
    except UserInactiveError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "account_inactive", "message": str(exc)},
        )
    except UserUnverifiedError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "account_unverified", "message": str(exc)},
        )
