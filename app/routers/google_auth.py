"""
Google Sign-In routes and Google Calendar connection management.

The OAuth `state` parameter is a signed, short-lived token rather than a
random value in server memory, so the flow survives the process restarting
mid-login and works on multi-instance deployments.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.routers.auth import _set_auth_cookie
from app.security import create_access_token
from app.services import google

router = APIRouter(tags=["google"])
logger = logging.getLogger(__name__)
settings = get_settings()

STATE_TTL_SECONDS = 600


def _issue_state(link_user_id: str | None = None) -> str:
    """Signed state: proves the callback belongs to a flow we started.

    Carries the user id when an existing account is linking Google, so the
    callback can tell 'link this account' apart from 'sign in'.
    """
    payload = {
        "nonce": secrets.token_urlsafe(16),
        "exp": datetime.now(timezone.utc) + timedelta(seconds=STATE_TTL_SECONDS),
    }
    if link_user_id:
        payload["link"] = link_user_id
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _read_state(state: str) -> dict:
    try:
        return jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired sign-in request") from exc


@router.get("/auth/google/status")
def google_status():
    return {"enabled": google.google_configured(), "calendar_sync": settings.GOOGLE_CALENDAR_SYNC}


@router.get("/auth/google/login")
def google_login(request: Request):
    if not google.google_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google Sign-In is not configured")
    return RedirectResponse(google.build_auth_url(_issue_state(), str(request.base_url)))


@router.get("/auth/google/link")
def google_link(request: Request, user: User = Depends(get_current_user)):
    """Connect Google to an account that already signed in with a password."""
    if not google.google_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google Sign-In is not configured")
    return RedirectResponse(google.build_auth_url(_issue_state(link_user_id=user.id), str(request.base_url)))


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        # User pressed Cancel on Google's consent screen.
        return RedirectResponse("/login?google_error=cancelled")
    if not code or not state:
        return RedirectResponse("/login?google_error=missing_code")

    claims = _read_state(state)

    try:
        tokens = google.exchange_code(code, str(request.base_url))
        info = google.fetch_userinfo(tokens["access_token"])
    except Exception as exc:
        logger.warning("Google OAuth exchange failed: %s: %s", type(exc).__name__, exc)
        return RedirectResponse("/login?google_error=exchange_failed")

    google_id = info.get("sub")
    email = (info.get("email") or "").lower()
    if not google_id or not email:
        return RedirectResponse("/login?google_error=no_email")
    if not info.get("email_verified", True):
        return RedirectResponse("/login?google_error=unverified_email")

    refresh_token = tokens.get("refresh_token")
    granted_calendar = google.CALENDAR_SCOPE in (tokens.get("scope") or "")

    linking_user_id = claims.get("link")
    if linking_user_id:
        user = db.get(User, linking_user_id)
        if not user:
            return RedirectResponse("/login?google_error=account_missing")
        clash = db.query(User).filter(User.google_id == google_id, User.id != user.id).first()
        if clash:
            return RedirectResponse("/profile?google_error=already_linked")
    else:
        # Match on google_id first, then fall back to email so a professor who
        # registered with a password can sign in with the same Google address.
        user = db.query(User).filter(User.google_id == google_id).first()
        if not user:
            user = db.query(User).filter(User.email == email).first()

        if not user:
            user = User(
                name=info.get("name") or email.split("@")[0],
                email=email,
                password_hash=None,  # Google-only account
                avatar_url=info.get("picture"),
            )
            db.add(user)

    if not user.is_active:
        return RedirectResponse("/login?google_error=deactivated")

    user.google_id = google_id
    user.avatar_url = info.get("picture") or user.avatar_url
    if refresh_token:
        user.google_refresh_token = google.encrypt_token(refresh_token)
        if granted_calendar:
            user.google_sync_enabled = True
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    response = RedirectResponse("/profile?google=linked" if linking_user_id else "/dashboard")
    _set_auth_cookie(response, create_access_token(user.id))
    return response


@router.post("/api/google/sync-now")
def sync_now(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Backfill upcoming events into Google Calendar."""
    if not user.google_sync_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Google Calendar is not connected")
    return google.sync_upcoming_events(db, user)


@router.post("/api/google/disconnect")
def google_disconnect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Unlink Google Calendar. Refuses if it would lock the account out."""
    if not user.password_hash and user.google_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This account signs in with Google only. Set a password first, or you'll be locked out.",
        )
    user.google_refresh_token = None
    user.google_sync_enabled = False
    user.google_id = None
    db.commit()
    return {"ok": True}


@router.post("/api/google/toggle-sync")
def toggle_sync(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    enabled = bool(payload.get("enabled"))
    if enabled and not user.google_refresh_token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Connect your Google account first")
    user.google_sync_enabled = enabled
    db.commit()
    return {"ok": True, "google_sync_enabled": user.google_sync_enabled}
