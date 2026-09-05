"""
Push subscription management and the subscribable calendar feed.

The calendar feed is intentionally unauthenticated: calendar clients
(Google Calendar, Apple Calendar, Outlook) cannot present a session cookie.
The secret token in the URL is the credential, it grants read-only access to
one professor's events, and it can be rotated from the profile page.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import Event, PushSubscription, User
from app.services.notifier import email_configured, push_configured, send_email, send_push_to_user


def _uid_domain() -> str:
    """The domain that makes a calendar UID globally unique.

    It must be one we actually control -- it was profschedule.ai, which is
    somebody else's. Changing it does mean a calendar already subscribed to
    this feed sees the events as new ones, so it is the sort of change that
    only gets cheaper the sooner it is made.
    """
    from app.config import get_settings

    settings = get_settings()
    base = settings.PUBLIC_BASE_URL or ""
    if settings.CANONICAL_HOST:
        return settings.CANONICAL_HOST
    if base:
        return base.split("//")[-1].split("/")[0]
    return "profschedule.org"


router = APIRouter(prefix="/api", tags=["notifications"])
logger = logging.getLogger(__name__)
CRLF_SPACE = chr(13) + chr(10) + " "  # RFC 5545 line-fold separator
settings = get_settings()


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: PushKeys


@router.get("/push/public-key")
def push_public_key():
    """VAPID public key the browser needs to create a subscription."""
    return {"public_key": settings.VAPID_PUBLIC_KEY, "enabled": push_configured()}


@router.post("/push/subscribe", status_code=status.HTTP_201_CREATED)
def push_subscribe(
    payload: PushSubscribeRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == payload.endpoint).first()
    if existing:
        # Re-subscribing on the same device (or after it moved accounts):
        # refresh the keys and ownership rather than creating a duplicate.
        existing.user_id = user.id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        db.commit()
        return {"ok": True, "updated": True}

    db.add(
        PushSubscription(
            user_id=user.id,
            endpoint=payload.endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            user_agent=(request.headers.get("user-agent") or "")[:255],
        )
    )
    db.commit()
    return {"ok": True, "updated": False}


@router.post("/push/unsubscribe")
def push_unsubscribe(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    endpoint = payload.get("endpoint")
    if endpoint:
        db.query(PushSubscription).filter(
            PushSubscription.endpoint == endpoint, PushSubscription.user_id == user.id
        ).delete()
        db.commit()
    return {"ok": True}


def _device_label(user_agent: str | None) -> str:
    """A recognisable name for a registered device, from its user agent."""
    ua = user_agent or ""
    if "iPhone" in ua:
        os_name = "iPhone"
    elif "iPad" in ua:
        os_name = "iPad"
    elif "Android" in ua:
        os_name = "Android"
    elif "Windows" in ua:
        os_name = "Windows PC"
    elif "Mac" in ua:
        os_name = "Mac"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Unknown device"

    # Order matters: Edge and Chrome both claim "Chrome", Chrome claims "Safari".
    for token, name in (("Edg", "Edge"), ("OPR", "Opera"), ("Firefox", "Firefox"),
                        ("Chrome", "Chrome"), ("Safari", "Safari")):
        if token in ua:
            return f"{os_name} · {name}"
    return os_name


@router.get("/push/devices")
def list_push_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Which devices will actually receive a push.

    Without this the professor has no way to tell "my phone is subscribed"
    from "I tapped Enable on my laptop and it silently failed" -- both look
    identical from the app.
    """
    subs = (
        db.query(PushSubscription)
        .filter(PushSubscription.user_id == user.id)
        .order_by(PushSubscription.created_at.desc())
        .all()
    )
    return {
        "devices": [
            {
                "id": s.id,
                "label": _device_label(s.user_agent),
                # Enough for the browser to recognise its own subscription
                # without handing back the full capability URL.
                "endpoint_tail": s.endpoint[-12:],
                "added": (s.created_at if s.created_at.tzinfo else s.created_at.replace(tzinfo=timezone.utc)).isoformat(),
            }
            for s in subs
        ]
    }


@router.delete("/push/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_push_device(device_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Forget one device -- e.g. a laptop whose subscription never worked."""
    sub = (
        db.query(PushSubscription)
        .filter(PushSubscription.id == device_id, PushSubscription.user_id == user.id)
        .first()
    )
    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    db.delete(sub)
    db.commit()
    return None


@router.post("/notifications/test")
def send_test_notification(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Fire a reminder through every enabled channel right now, so a professor
    can confirm delivery works before relying on it."""
    results = {"email": "skipped", "push": "skipped"}

    if user.notify_email:
        if not email_configured():
            results["email"] = "not configured on the server"
        else:
            from app.services.notifier import reminder_email_html

            ok = send_email(
                user.email,
                "🔔 ProfSchedule AI test notification",
                "This is a test. Your email reminders are working.",
                reminder_email_html("Test notification", "Right now", None),
            )
            results["email"] = "sent" if ok else "failed"

    if user.notify_push:
        if not push_configured():
            results["push"] = "not configured on the server"
        else:
            n = send_push_to_user(db, user, "🔔 Test notification", "Your push reminders are working.")
            devices = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
            results["push"] = f"sent to {n} device(s)" if n else (
                "no devices registered — enable notifications on your phone first" if devices == 0 else "failed"
            )

    results["devices"] = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
    return results


# ---------------------------------------------------------------------------
# Subscribable calendar feed
# ---------------------------------------------------------------------------
def _fold(line: str) -> str:
    """Fold to 75 octets per RFC 5545.

    Apple Calendar is stricter than Google here: an over-long SUMMARY can
    make it reject the whole event rather than just truncating it.
    """
    if len(line.encode("utf-8")) <= 75:
        return line

    out, chunk = [], b""
    for ch in line:
        enc = ch.encode("utf-8")
        # Continuation lines begin with a space, so they get one octet less.
        limit = 75 if not out else 74
        if len(chunk) + len(enc) > limit:
            out.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += enc
    if chunk:
        out.append(chunk.decode("utf-8"))
    return CRLF_SPACE.join(out)


def _ics_escape(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\n", "\\n")
    )


@router.get("/calendar/{token}.ics")
def calendar_feed(token: str, db: Session = Depends(get_db)):
    """Read-only ICS feed for one professor, addressed by secret token.

    Each event carries a VALARM, so the subscribing calendar app raises its
    own native reminder on the phone — no push infrastructure required.
    """
    user = db.query(User).filter(User.calendar_token == token).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Calendar not found")

    now = datetime.now(timezone.utc)
    events = (
        db.query(Event)
        .filter(
            Event.user_id == user.id,
            Event.is_cancelled.is_(False),
            Event.start_datetime >= now - timedelta(days=30),
            Event.start_datetime <= now + timedelta(days=180),
        )
        .order_by(Event.start_datetime)
        .all()
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ProfSchedule AI//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(user.name)} — Teaching Schedule",
        f"X-WR-TIMEZONE:{user.timezone}",
        # Hint to clients how often to re-poll the feed.
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
        "X-APPLE-CALENDAR-COLOR:#E0785D",
        f"X-WR-CALDESC:Classes and meetings from ProfSchedule AI",
    ]

    lead = user.default_reminder_minutes or 30
    for e in events:
        start = e.start_datetime if e.start_datetime.tzinfo else e.start_datetime.replace(tzinfo=timezone.utc)
        end = e.end_datetime if e.end_datetime.tzinfo else e.end_datetime.replace(tzinfo=timezone.utc)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e.id}@{_uid_domain()}",
            f"DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{_ics_escape(e.title)}",
            f"LOCATION:{_ics_escape(e.location or '')}",
            f"DESCRIPTION:{_ics_escape(e.description or e.subject or '')}",
            f"CATEGORIES:{_ics_escape(e.event_type)}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "SEQUENCE:0",
            # RELATED=START is implied by the spec, but stating it explicitly
            # avoids ambiguity in stricter clients such as Apple Calendar.
            "BEGIN:VALARM",
            f"TRIGGER;RELATED=START:-PT{lead}M",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_escape(e.title)}",
            "END:VALARM",
            # A second alert at start time, so a missed lead-time alarm still
            # surfaces something.
            "BEGIN:VALARM",
            "TRIGGER;RELATED=START:PT0M",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_escape(e.title)} is starting now",
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="profschedule.ics"', "Cache-Control": "max-age=900"},
    )


@router.get("/calendar-feed-url")
def calendar_feed_url(request: Request, user: User = Depends(get_current_user)):
    base = (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    return {"url": f"{base}/api/calendar/{user.calendar_token}.ics"}


@router.post("/calendar-feed-url/rotate")
def rotate_calendar_token(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Invalidate the old feed link (e.g. if it was shared by accident)."""
    import uuid

    user.calendar_token = uuid.uuid4().hex
    db.commit()
    return {"ok": True, "token": user.calendar_token}


# ---------------------------------------------------------------------------
# First-run onboarding
# ---------------------------------------------------------------------------
@router.get("/onboarding/status")
def onboarding_status(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """What the professor still needs to set up for reminders to reach them.

    Drives the first-login prompt, so nobody has to discover the settings
    page on their own.
    """
    from app.services.google import google_configured

    base = (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    devices = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()

    return {
        "completed": user.onboarding_completed,
        "email": {"address": user.email, "enabled": user.notify_email, "server_ready": email_configured()},
        "push": {"enabled": user.notify_push, "server_ready": push_configured(), "devices": devices},
        "google": {"available": google_configured(), "connected": user.google_sync_enabled},
        "calendar_feed_url": f"{base}/api/calendar/{user.calendar_token}.ics",
        # Nothing left to prompt about once a calendar is linked and this
        # device can receive push.
        "needs_setup": not user.onboarding_completed and (devices == 0 or not user.google_sync_enabled),
    }


@router.post("/onboarding/complete")
def onboarding_complete(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user.onboarding_completed = True
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# The bell, in one request
# ---------------------------------------------------------------------------
@router.get("/notifications/feed")
def notification_feed(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Both notification feeds, together.

    The bell shows one merged list, but the browser was fetching the two
    halves separately every minute: two requests, two session lookups, six
    queries, for one badge that is usually unchanged. At a hundred thousand
    signed-in tabs that is thousands of requests a second spent confirming
    that nothing has happened.

    This calls the two existing handlers rather than reimplementing them. They
    remain the source of truth and remain reachable on their own URLs -- which
    is what the clear, dismiss and mark-read paths still use -- so there is no
    second copy of the rules here to drift out of step with them. In
    particular the personal feed still flushes that user's overdue reminders
    on the way past, which is what keeps the bell honest on an instance that
    has been asleep.

    The response carries an ETag. Nothing happens on most ticks, so most
    replies are byte-for-byte the previous one; a 304 turns those into a bare
    header exchange. It does not save the queries -- the answer has to be
    computed before we know it is unchanged -- but it saves the body on the
    wire and the re-render at the other end.
    """
    from app.routers.reminders import notifications as personal_feed
    from app.routers.work import work_notifications as work_feed

    payload = {
        "work": work_feed(db=db, user=user),
        "personal": personal_feed(db=db, user=user),
    }

    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    etag = '"' + hashlib.blake2b(body.encode("utf-8"), digest_size=16).hexdigest() + '"'

    # no-cache, not no-store: the browser should keep the copy and revalidate,
    # which is the whole mechanism that produces the 304.
    headers = {"ETag": etag, "Cache-Control": "no-cache, private"}

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    return Response(body, media_type="application/json", headers=headers)
