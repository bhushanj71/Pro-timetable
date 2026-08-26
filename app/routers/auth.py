"""
Registration, login, logout. Issues a JWT stored in an httpOnly cookie.
"""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import COOKIE_NAME, get_current_user
from app.models import User
from app.rate_limit import login_limiter, register_limiter
from app.schemas import Token, UserCreate, UserLogin, UserOut, UserProfileUpdate
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, request: Request, response: Response, db: Session = Depends(get_db)):
    register_limiter.check(request)
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An account with this email already exists")

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        timezone=payload.timezone,
        working_hours_start=payload.working_hours_start,
        working_hours_end=payload.working_hours_end,
        lunch_start=payload.lunch_start,
        lunch_end=payload.lunch_end,
        working_days=payload.working_days,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    _set_auth_cookie(response, token)
    return user


@router.post("/login", response_model=UserOut)
def login(payload: UserLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    # Throttled per address per email, so guessing one account's password
    # cannot be run at network speed, and hammering many accounts from one
    # address is limited too.
    login_limiter.check(request, payload.email.lower())

    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        # Deliberately identical for "no such user" and "wrong password":
        # distinguishing them would confirm which addresses have accounts.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")

    login_limiter.reset(request, payload.email.lower())
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(user.id)
    _set_auth_cookie(response, token)
    return user


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# Departments common to Indian engineering and science faculties. A list, not
# a lookup table: the point is that colleagues at one college pick the same
# words, so the directory can group them. Anything not here is still typeable.
DEPARTMENTS = [
    "Computer Engineering",
    "Computer Science & Engineering",
    "Information Technology",
    "Artificial Intelligence & Data Science",
    "Electronics & Telecommunication",
    "Electronics Engineering",
    "Electrical Engineering",
    "Mechanical Engineering",
    "Civil Engineering",
    "Chemical Engineering",
    "Instrumentation Engineering",
    "Production Engineering",
    "Automobile Engineering",
    "Biotechnology",
    "Applied Sciences & Humanities",
    "Mathematics",
    "Physics",
    "Chemistry",
    "Management Studies (MBA)",
    "Computer Applications (MCA)",
    "Pharmacy",
    "Architecture",
    "Physical Education",
    "Library",
    "Administration",
]

# Mail providers that say nothing about where someone works. Without this,
# every Gmail user would be treated as a colleague of every other one.
_PUBLIC_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com",
    "hotmail.com", "live.com", "icloud.com", "me.com", "proton.me",
    "protonmail.com", "rediffmail.com", "aol.com", "zoho.com", "yandex.com",
    "mail.com", "gmx.com", "fastmail.com",
}


def email_domain(email: str) -> str | None:
    """The institutional part of an address, or None if it is a public inbox.

    The character check is not cosmetic: this value is interpolated into a
    LIKE pattern, and % or _ in it would silently widen the match to other
    domains. A real domain cannot contain either, so anything that does is
    rejected rather than escaped.
    """
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[-1].strip().lower()
    if "." not in domain or domain in _PUBLIC_MAIL_DOMAINS:
        return None
    return domain if re.fullmatch(r"[a-z0-9.-]+", domain) else None


@router.get("/profile-options")
def profile_options(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Options for the college and department fields.

    College suggestions come only from accounts sharing the caller's email
    domain, never from the whole database. Two reasons. It keeps the list
    short and correct -- the colleagues whose spelling actually needs matching
    are the ones at the same institution. And a dropdown built from every
    account would publish the list of institutions using this app to anyone
    who signs up, and hand them the exact string needed to point a community
    directory at a college they have nothing to do with.

    A public inbox (gmail and friends) yields no domain and therefore no
    suggestions: those addresses say nothing about where someone works.
    """
    domain = email_domain(user.email)

    colleges: list[str] = []
    if domain:
        rows = (
            db.query(User.college)
            .filter(
                User.college.isnot(None),
                User.college != "",
                func.lower(User.email).like(f"%@{domain}"),
            )
            .distinct()
            .all()
        )
        # Collapsed case-insensitively so one college does not appear twice
        # because two people capitalised it differently.
        seen: dict[str, str] = {}
        for (name,) in rows:
            seen.setdefault(name.strip().lower(), name.strip())
        colleges = sorted(seen.values(), key=str.lower)

    return {
        "departments": DEPARTMENTS,
        "colleges": colleges,
        "college_domain": domain,
        "current": {"department": user.department, "college": user.college},
    }


@router.put("/me", response_model=UserOut)
def update_profile(payload: UserProfileUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    fields = payload.model_dump(exclude_unset=True)

    # Typing a college that a colleague already uses, in different case or with
    # stray spacing, stores their spelling rather than a second variant. The
    # directory matches case-insensitively either way; this keeps the value
    # people actually read consistent.
    if fields.get("college"):
        fields["college"] = _canonical_college(db, user, fields["college"])

    for field, value in fields.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


def _canonical_college(db: Session, user: User, typed: str) -> str:
    typed = typed.strip()
    domain = email_domain(user.email)
    if not domain or not typed:
        return typed
    match = (
        db.query(User.college)
        .filter(
            User.college.isnot(None),
            func.lower(func.trim(User.college)) == typed.lower(),
            func.lower(User.email).like(f"%@{domain}"),
            User.id != user.id,
        )
        .first()
    )
    return match[0].strip() if match and match[0] else typed


@router.post("/token", response_model=Token)
def token_login(payload: UserLogin, db: Session = Depends(get_db)):
    """Pure API login for external clients that want a Bearer token instead of a cookie."""
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return Token(access_token=create_access_token(user.id))
