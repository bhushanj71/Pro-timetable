"""
Google Sign-In and Google Calendar sync.

Sign-in uses the OAuth 2.0 authorization-code flow. The code is exchanged
server-side over TLS directly with Google, and the profile is read from the
userinfo endpoint — so no local JWT signature verification is needed.

Requesting offline access additionally yields a refresh token, which lets the
app write events into the professor's own Google Calendar later, without them
being present. Google then delivers the reminder to their phone natively,
which is far more reliable than us running our own push infrastructure.

Refresh tokens are encrypted at rest with a key derived from SECRET_KEY: a
leaked database row must not hand over ongoing access to someone's calendar.
"""
import base64
import hashlib
import logging
from datetime import datetime, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event, User

logger = logging.getLogger(__name__)
settings = get_settings()

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

BASE_SCOPES = ["openid", "email", "profile"]
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def google_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def scopes() -> list[str]:
    return BASE_SCOPES + ([CALENDAR_SCOPE] if settings.GOOGLE_CALENDAR_SYNC else [])


def redirect_uri(request_base: str) -> str:
    base = (settings.PUBLIC_BASE_URL or request_base).rstrip("/")
    return f"{base}/auth/google/callback"


# ---------------------------------------------------------------------------
# Refresh-token encryption
# ---------------------------------------------------------------------------
def _fernet() -> Fernet:
    # Fernet needs a 32-byte urlsafe-base64 key; derive one deterministically
    # from SECRET_KEY so no extra secret has to be managed.
    digest = hashlib.sha256((settings.SECRET_KEY or "dev").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(blob: str | None) -> str | None:
    if not blob:
        return None
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except (InvalidToken, ValueError):
        # SECRET_KEY changed, or the row predates encryption — force re-link.
        logger.warning("Stored Google refresh token could not be decrypted")
        return None


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------
def build_auth_url(state: str, request_base: str) -> str:
    from urllib.parse import urlencode

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(request_base),
        "response_type": "code",
        "scope": " ".join(scopes()),
        "state": state,
        # offline + consent is what actually returns a refresh token; without
        # prompt=consent Google omits it on repeat authorisations.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code(code: str, request_base: str) -> dict:
    resp = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri(request_base),
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_userinfo(access_token: str) -> dict:
    resp = httpx.get(USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}, timeout=20)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(user: User) -> str | None:
    """Exchange the stored refresh token for a short-lived access token."""
    refresh = decrypt_token(user.google_refresh_token)
    if not refresh:
        return None
    try:
        resp = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "refresh_token": refresh,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except httpx.HTTPError as exc:
        logger.warning("Google token refresh failed for %s: %s", user.email, exc)
        return None


# ---------------------------------------------------------------------------
# Calendar sync
# ---------------------------------------------------------------------------
def _event_body(event: Event, user: User) -> dict:
    start = event.start_datetime if event.start_datetime.tzinfo else event.start_datetime.replace(tzinfo=timezone.utc)
    end = event.end_datetime if event.end_datetime.tzinfo else event.end_datetime.replace(tzinfo=timezone.utc)
    lead = user.default_reminder_minutes or 30

    return {
        "summary": event.title,
        "description": event.description or event.subject or "",
        "location": event.location or "",
        "start": {"dateTime": start.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        "source": {"title": "ProfSchedule AI", "url": (settings.PUBLIC_BASE_URL or "https://www.profschedule.org")},
        "reminders": {
            "useDefault": False,
            # popup is what surfaces on the phone's lock screen via the
            # Google Calendar app.
            "overrides": [
                {"method": "popup", "minutes": lead},
                {"method": "popup", "minutes": 5},
            ],
        },
    }


def sync_event(db: Session, user: User, event: Event) -> bool:
    """Create or update the mirrored event in the professor's Google Calendar."""
    if not (google_configured() and user.google_sync_enabled):
        return False

    token = refresh_access_token(user)
    if not token:
        return False

    calendar_id = user.google_calendar_id or "primary"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = _event_body(event, user)

    try:
        if event.google_event_id:
            resp = httpx.patch(
                f"{CALENDAR_API}/calendars/{calendar_id}/events/{event.google_event_id}",
                json=body, headers=headers, timeout=20,
            )
            # The user may have deleted it on Google's side; recreate instead
            # of failing forever.
            if resp.status_code in (404, 410):
                event.google_event_id = None
            else:
                resp.raise_for_status()
                return True

        resp = httpx.post(
            f"{CALENDAR_API}/calendars/{calendar_id}/events", json=body, headers=headers, timeout=20
        )
        resp.raise_for_status()
        event.google_event_id = resp.json().get("id")
        db.commit()
        return True
    except httpx.HTTPError as exc:
        logger.warning("Google Calendar sync failed for event %s: %s", event.id, exc)
        return False


def delete_event(user: User, google_event_id: str) -> bool:
    if not (google_configured() and user.google_sync_enabled and google_event_id):
        return False
    token = refresh_access_token(user)
    if not token:
        return False

    calendar_id = user.google_calendar_id or "primary"
    try:
        resp = httpx.delete(
            f"{CALENDAR_API}/calendars/{calendar_id}/events/{google_event_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        # Already gone is a success from our point of view.
        return resp.status_code in (200, 204, 404, 410)
    except httpx.HTTPError as exc:
        logger.warning("Google Calendar delete failed: %s", exc)
        return False


def sync_upcoming_events(db: Session, user: User, limit: int = 200) -> dict:
    """Push every upcoming event to Google Calendar — used for the initial
    backfill right after a professor connects their account."""
    if not (google_configured() and user.google_sync_enabled):
        return {"synced": 0, "failed": 0, "reason": "sync not enabled"}

    events = (
        db.query(Event)
        .filter(
            Event.user_id == user.id,
            Event.is_cancelled.is_(False),
            Event.start_datetime >= datetime.now(timezone.utc),
        )
        .order_by(Event.start_datetime)
        .limit(limit)
        .all()
    )

    synced = failed = 0
    for event in events:
        if sync_event(db, user, event):
            synced += 1
        else:
            failed += 1
    db.commit()
    return {"synced": synced, "failed": failed, "total": len(events)}
