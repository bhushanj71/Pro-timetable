"""
Shared FastAPI dependencies: DB session, current-user resolution.

Auth uses an httpOnly cookie holding a JWT (works for both the
server-rendered pages and same-origin fetch/HTMX API calls) with an
optional Authorization: Bearer fallback for pure API clients.
"""
from fastapi import Cookie, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import decode_access_token
from app.services import admin_scope

COOKIE_NAME = "access_token"


def _extract_token(access_token: str | None, authorization: str | None) -> str | None:
    if access_token:
        return access_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    return None


def get_current_user(
    db: Session = Depends(get_db),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> User:
    token = _extract_token(access_token, authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Gate admin-only endpoints. Deliberately a separate dependency so a
    missing decorator can never silently expose an admin route.

    Still means super admin, and still guards the platform-level acts:
    appointing a college administrator, and anything that reaches across
    colleges. Routes a college administrator may also use take
    `get_panel_admin` and scope themselves through app.services.admin_scope.
    """
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user


def get_panel_admin(user: User = Depends(get_current_user)) -> User:
    """Gate the admin panel for either kind of administrator.

    This only says someone has *a* panel. It says nothing about whose records
    they may read or write, so every route using it must narrow itself through
    admin_scope -- the dependency opens the door, the scope decides the room.
    """
    if not admin_scope.is_panel_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user


def get_current_user_optional(
    db: Session = Depends(get_db),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> User | None:
    token = _extract_token(access_token, authorization)
    if not token:
        return None
    user_id = decode_access_token(token)
    if not user_id:
        return None
    return db.get(User, user_id)
