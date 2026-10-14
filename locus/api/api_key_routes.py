"""
HTTP endpoints for self-service API key management.

Endpoints:
POST   /api-keys          - Generate a new API key (returns raw key once only)
GET    /api-keys          - List the authenticated user's API keys (metadata only)
GET    /api-keys/{key_id} - View metadata for a specific key
DELETE /api-keys/{key_id} - Revoke an API key
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from locus.api.dependencies import get_api_key_service, get_current_user
from locus.api.schemas import (
    APIKeyResponse,
    CreateAPIKeyRequest,
    CreatedAPIKeyResponse,
)
from locus.domain.api_key_service import (
    APIKeyNotFoundError,
    APIKeyService,
    InvalidKeyNameError,
)
from locus.domain.models import APIKey, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _to_api_key_response(key: APIKey) -> APIKeyResponse:
    """Serialize internal APIKey dataclass to public metadata response schema."""
    now = datetime.now(timezone.utc)
    return APIKeyResponse(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        created_at=key.created_at,
        expires_at=key.expires_at,
        revoked_at=key.revoked_at,
        is_active=key.is_valid(now),
    )


@router.post(
    "",
    response_model=CreatedAPIKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new API key",
)
async def create_api_key(
    body: CreateAPIKeyRequest,
    current_user: User = Depends(get_current_user),
    key_service: APIKeyService = Depends(get_api_key_service),
) -> CreatedAPIKeyResponse:
    """Create a new API key for the authenticated user.

    The raw key secret is returned in the response ONCE. It cannot be retrieved
    or viewed again later. Store it securely.
    """
    try:
        api_key, raw_key = await key_service.create_key(
            user_id=current_user.id,
            name=body.name,
            expires_in_days=body.expires_in_days,
        )
    except InvalidKeyNameError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_key_name", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_expiry", "message": str(exc)},
        )

    return CreatedAPIKeyResponse(
        id=api_key.id,
        name=api_key.name,
        key=raw_key,
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )


@router.get(
    "",
    response_model=list[APIKeyResponse],
    status_code=status.HTTP_200_OK,
    summary="List user's API keys",
)
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    key_service: APIKeyService = Depends(get_api_key_service),
) -> list[APIKeyResponse]:
    """List all API keys belonging to the authenticated user.

    Returns safe metadata only. Raw keys and hashes are never exposed.
    """
    keys = await key_service.list_keys(current_user.id)
    return [_to_api_key_response(k) for k in keys]


@router.get(
    "/{key_id}",
    response_model=APIKeyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get API key metadata",
)
async def get_api_key_metadata(
    key_id: UUID,
    current_user: User = Depends(get_current_user),
    key_service: APIKeyService = Depends(get_api_key_service),
) -> APIKeyResponse:
    """Retrieve metadata for a specific API key belonging to the authenticated user."""
    try:
        key = await key_service.get_key(key_id=key_id, user_id=current_user.id)
    except APIKeyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "API key not found."},
        )

    return _to_api_key_response(key)


@router.delete(
    "/{key_id}",
    response_model=APIKeyResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke an API key",
)
async def revoke_api_key(
    key_id: UUID,
    current_user: User = Depends(get_current_user),
    key_service: APIKeyService = Depends(get_api_key_service),
) -> APIKeyResponse:
    """Revoke an API key belonging to the authenticated user.

    Revocation is permanent and immediate. Revoked keys cannot be used to
    authenticate subsequent requests.
    """
    try:
        revoked_key = await key_service.revoke_key(
            key_id=key_id, user_id=current_user.id
        )
    except APIKeyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "API key not found."},
        )

    return _to_api_key_response(revoked_key)
