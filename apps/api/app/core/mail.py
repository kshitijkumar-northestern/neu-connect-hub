"""Outgoing email. Message bodies hold single-use links, so they are never logged."""

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.core.config import Settings

logger = logging.getLogger("app.mail")

SMTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class DevMailer:
    """Writes each message to a file in a local, gitignored folder."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._write, message)

    def _write(self, message: EmailMessage) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        path = self.directory / f"{stamp}-{uuid4().hex[:8]}.txt"
        path.write_text(f"To: {message.to}\nSubject: {message.subject}\n\n{message.text}\n")
        logger.info("Development email written", extra={"file": path.name})


class SmtpMailer:
    """Sends through any SMTP provider over STARTTLS; EMAIL_PROVIDER_API_KEY is the password."""

    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host:
            raise ValueError("SMTP_HOST is required for the smtp mail backend")
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.username = settings.smtp_username
        self.password = (
            settings.email_provider_api_key.get_secret_value()
            if settings.email_provider_api_key
            else None
        )
        self.sender = settings.mail_from

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self.sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        with smtplib.SMTP(self.host, self.port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(mime)


async def deliver(mailer: Mailer, message: EmailMessage) -> None:
    """Send after the response has gone out. Failures are logged, never raised: the account change
    is already saved, the response must not differ, and the user can ask for another link."""
    try:
        await mailer.send(message)
    except Exception:
        logger.exception("Sending email failed", extra={"subject": message.subject})


def create_mailer(settings: Settings) -> Mailer:
    if settings.mail_backend == "smtp":
        return SmtpMailer(settings)
    return DevMailer(settings.dev_mail_dir)
