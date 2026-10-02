"""Verification email for self-service sign-up.

With SMTP_HOST configured, the code is sent over SMTP (STARTTLS by default)
using only the standard library. Without it, a mock sender prints the code to
the API's log so sign-up can be tried locally; nothing leaves the machine.
The SMTP password and the code are never included in errors.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger("gtmflow.email")


class EmailDeliveryError(Exception):
    pass


def delivery_mode() -> str:
    return "smtp" if settings.smtp_host else "mock"


def send_verification_code(to: str, code: str, expires_minutes: int) -> None:
    subject = f"Your GTMFlow verification code: {code}"
    body = (
        f"Your GTMFlow verification code is {code}.\n\n"
        f"It expires in {expires_minutes} minutes. If you did not try to create an account, ignore this email.\n"
    )
    if delivery_mode() == "mock":
        # Local development only: there is no mail server, so show the code
        # where the developer running the API can read it.
        print(f"[mock email] verification code for {to}: {code}", flush=True)
        logger.info("mock verification email prepared for %s", to)
        return
    message = EmailMessage()
    message["From"] = settings.email_from or settings.smtp_username
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        logger.warning("verification email to %s failed: %s", to, type(error).__name__)
        raise EmailDeliveryError("The verification email could not be sent.") from None
