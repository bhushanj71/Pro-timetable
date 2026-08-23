"""
Multi-channel reminder delivery.

A reminder fans out to whichever channels the professor has enabled:

  in-app  — always; the notification centre reads delivered reminders
  email   — to the address they signed in with, so the phone's mail app
            raises its own notification
  push    — Web Push to every registered device, which appears on the lock
            screen even when the site is closed

Each channel fails independently: a broken SMTP config must never stop a
push from going out, and vice versa.
"""
import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PushSubscription, User

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def email_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD)


def send_email(to_email: str, subject: str, body_text: str, body_html: str | None = None) -> bool:
    if not email_configured():
        logger.debug("Email skipped for %s: SMTP is not configured", to_email)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
        msg["To"] = to_email
        msg.attach(MIMEText(body_text, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(msg["From"], [to_email], msg.as_string())
        return True
    except Exception as exc:
        logger.warning("Email to %s failed: %s: %s", to_email, type(exc).__name__, exc)
        return False


def reminder_email_html(title: str, when_text: str, location: str | None) -> str:
    """Small, inline-styled email — mail clients strip <style> blocks."""
    loc = (
        f'<p style="margin:4px 0 0;color:#6b6058;font-size:14px">📍 {location}</p>'
        if location else ""
    )
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#fdf9f7;padding:24px">
  <div style="max-width:480px;margin:0 auto;background:#fff;border:1px solid #f2e7e1;border-radius:16px;padding:24px">
    <div style="font-size:13px;font-weight:700;color:#e0785d;letter-spacing:.04em">🔔 PROFSCHEDULE AI REMINDER</div>
    <h2 style="margin:10px 0 6px;font-size:20px;color:#2f2a27">{title}</h2>
    <p style="margin:0;color:#6b6058;font-size:15px">🕐 {when_text}</p>
    {loc}
    <p style="margin:20px 0 0;font-size:12px;color:#9c8e85">
      You're receiving this because email reminders are on in your ProfSchedule AI profile.
    </p>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Web Push
# ---------------------------------------------------------------------------
def push_configured() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def send_push_to_user(db: Session, user: User, title: str, body: str, url: str = "/dashboard") -> int:
    """Push to every device the professor registered. Returns how many succeeded.

    Endpoints that the push service reports as gone (404/410) are deleted, so
    stale devices don't accumulate.
    """
    if not push_configured():
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush is not installed; push notifications are unavailable")
        return 0

    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).all()
    if not subs:
        return 0

    payload = json.dumps({"title": title, "body": body, "url": url})
    sent, dead = 0, []

    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{settings.VAPID_CONTACT_EMAIL or 'admin@example.com'}"},
                timeout=15,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                dead.append(sub)          # unsubscribed or expired
            else:
                logger.warning("Push failed (%s): %s", status, exc)
        except Exception as exc:
            logger.warning("Push error: %s: %s", type(exc).__name__, exc)

    for sub in dead:
        db.delete(sub)
    if dead:
        db.commit()
        logger.info("Removed %d expired push subscription(s)", len(dead))

    return sent
