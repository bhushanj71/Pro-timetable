"""
Provision the first administrator from environment variables at startup.

There is deliberately no self-service way to become an admin, and hosts
without shell access (Render's free tier and most PaaS free plans) cannot
run create_admin.py. Without this, a fresh production database would have
no way to reach the admin panel at all.

Behaviour, given BOOTSTRAP_ADMIN_EMAIL:
  - account exists      -> promote it to admin (and reactivate it)
  - account missing and
    a password is set   -> create the admin account
  - account missing and
    no password         -> log a hint and do nothing

Safe to run on every boot: it never downgrades anyone, never overwrites an
existing password, and does nothing once the account is already an admin.
"""
import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.security import hash_password

logger = logging.getLogger(__name__)
settings = get_settings()


def bootstrap_admin(db: Session) -> None:
    email = (settings.BOOTSTRAP_ADMIN_EMAIL or "").strip().lower()
    if not email:
        return

    password = settings.BOOTSTRAP_ADMIN_PASSWORD or ""
    user = db.query(User).filter(User.email == email).first()

    if user:
        if user.is_admin and user.is_active:
            return  # already provisioned; nothing to do
        user.is_admin = True
        user.is_active = True
        db.commit()
        logger.info("Bootstrap: promoted existing account %s to administrator", email)
        return

    if len(password) < 8:
        logger.warning(
            "Bootstrap: no account for %s yet. Either register that email in the app "
            "(it will be promoted on the next restart), or set BOOTSTRAP_ADMIN_PASSWORD "
            "(min 8 characters) to have it created automatically.",
            email,
        )
        return

    db.add(
        User(
            name="Administrator",
            email=email,
            password_hash=hash_password(password),
            is_admin=True,
            is_active=True,
        )
    )
    db.commit()
    logger.info(
        "Bootstrap: created administrator %s. Sign in, change the password, then remove "
        "BOOTSTRAP_ADMIN_PASSWORD from the environment.",
        email,
    )
