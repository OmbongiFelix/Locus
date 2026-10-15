"""
HTTP endpoints for user registration, email verification, and authentication.

Endpoints:
POST /auth/register             - Register a new account
POST /auth/verify-email         - Verify email with single-use token
POST /auth/resend-verification  - Resend email verification token
POST /auth/login                - Secure login, returns JWT access token
GET  /auth/me                   - Current authenticated user profile
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status

from locus.api.dependencies import (
    auth_rate_limiter,
    get_auth_service,
    get_current_user,
)
from locus.api.schemas import (
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResendVerificationRequest,
    TokenResponse,
    UserResponse,
    VerifyEmailRequest,
)
from locus.config import get_settings
from locus.domain.auth_service import (
    AccountInactiveError,
    AccountUnverifiedError,
    AuthService,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    InvalidEmailError,
    InvalidTokenError,
    TokenAlreadyUsedError,
    TokenExpiredError,
    WeakPasswordError,
)
from locus.domain.models import User
from locus.security import create_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_user_response(user: User) -> UserResponse:
    """Serialize internal User dataclass to public UserResponse schema."""
    return UserResponse(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        is_verified=user.is_verified,
        created_at=user.created_at,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    body: RegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
    _rate: None = Depends(auth_rate_limiter),
) -> UserResponse:
    """Register a new user account.

    An unverified account is created and a single-use verification token is
    dispatched via the configured email provider.
    """
    try:
        user, _raw_token = await auth_service.register(
            email=body.email, password=body.password
        )
    except EmailAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "email_already_registered", "message": str(exc)},
        )
    except (WeakPasswordError, InvalidEmailError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_input", "message": str(exc)},
        )

    return _to_user_response(user)


@router.post(
    "/verify-email",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify user email address",
)
async def verify_email(
    body: VerifyEmailRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    """Verify an email address using a cryptographically secure, single-use token."""
    try:
        user = await auth_service.verify_email(body.token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_token", "message": str(exc)},
        )
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "token_expired", "message": str(exc)},
        )
    except TokenAlreadyUsedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "token_already_used", "message": str(exc)},
        )

    return _to_user_response(user)


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend email verification token",
)
async def resend_verification(
    body: ResendVerificationRequest,
    auth_service: AuthService = Depends(get_auth_service),
    _rate: None = Depends(auth_rate_limiter),
) -> MessageResponse:
    """Request a fresh email verification token.

    Always returns a generic confirmation message to prevent account enumeration.
    """
    await auth_service.resend_verification(body.email)
    return MessageResponse(
        message="If an unverified account with this email exists, a new verification link has been sent."
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Sign in and obtain access token",
)
async def login(
    body: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
    _rate: None = Depends(auth_rate_limiter),
) -> TokenResponse:
    """Authenticate with email and password to receive a bearer access token."""
    try:
        user = await auth_service.authenticate(body.email, body.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_credentials", "message": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )
    except AccountInactiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "account_inactive", "message": str(exc)},
        )
    except AccountUnverifiedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "account_unverified", "message": str(exc)},
        )

    settings = get_settings()
    expire_minutes = getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 60)
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email},
        expires_delta=timedelta(minutes=expire_minutes),
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expire_minutes * 60,
    )


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get authenticated user profile",
)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Return the profile of the currently authenticated user."""
    return _to_user_response(current_user)
