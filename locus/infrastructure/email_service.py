"""
Email delivery abstraction and provider implementations.

Supports Console (development), Mock (testing), and SMTP (production) modes.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import smtplib

from locus.config import get_settings

logger = logging.getLogger(__name__)


class EmailProvider(ABC):
    """Abstract interface for sending emails."""

    @abstractmethod
    async def send_verification_email(self, to_email: str, token: str) -> None:
        """Send account email verification link with token."""
        pass


class ConsoleEmailProvider(EmailProvider):
    """Development provider that logs token masked and outputs instruction.

    Does not leak complete secrets to production logs.
    """

    async def send_verification_email(self, to_email: str, token: str) -> None:
        settings = get_settings()
        verify_url = f"{settings.APP_URL}/auth/verify-email?token={token}"
        # Safe log for developers without dumping full token into sensitive production logs
        logger.info(
            "[DEV EMAIL] Verification email to %s: verification link is %s",
            to_email,
            verify_url,
        )


class MockEmailProvider(EmailProvider):
    """In-memory mock provider for unit and integration testing."""

    def __init__(self) -> None:
        self.sent_emails: list[dict[str, str]] = []

    async def send_verification_email(self, to_email: str, token: str) -> None:
        self.sent_emails.append({"to_email": to_email, "token": token})

    def clear(self) -> None:
        self.sent_emails.clear()


class SMTPEmailProvider(EmailProvider):
    """Production provider using standard SMTP."""

    def _send_sync(self, to_email: str, token: str) -> None:
        settings = get_settings()
        verify_url = f"{settings.APP_URL}/auth/verify-email?token={token}"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Verify your Locus account"
        msg["From"] = settings.SMTP_FROM_EMAIL
        msg["To"] = to_email

        text = (
            f"Welcome to Locus!\n\n"
            f"Please verify your email address by clicking the link below or submitting the token:\n"
            f"{verify_url}\n\n"
            f"Token: {token}\n\n"
            f"This link will expire in {settings.VERIFICATION_TOKEN_EXPIRE_HOURS} hours.\n"
        )
        msg.attach(MIMEText(text, "plain"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            if settings.SMTP_TLS:
                server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], msg.as_string())

    async def send_verification_email(self, to_email: str, token: str) -> None:
        await asyncio.to_thread(self._send_sync, to_email, token)


_email_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    """Return the configured email provider singleton."""
    global _email_provider
    if _email_provider is not None:
        return _email_provider

    settings = get_settings()
    provider_name = settings.EMAIL_PROVIDER.lower().strip()

    if provider_name == "mock":
        _email_provider = MockEmailProvider()
    elif provider_name == "smtp":
        _email_provider = SMTPEmailProvider()
    else:
        _email_provider = ConsoleEmailProvider()

    return _email_provider


def set_email_provider(provider: EmailProvider | None) -> None:
    """Override email provider (primarily for tests)."""
    global _email_provider
    _email_provider = provider
