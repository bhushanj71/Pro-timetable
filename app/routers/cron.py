"""
Endpoint invoked periodically by Vercel Cron (see vercel.json) to process
due reminders. Protected by a shared secret since it has no user session.
"""
import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.reminder_service import process_due_reminders

router = APIRouter(prefix="/api/cron", tags=["cron"])
settings = get_settings()
logger = logging.getLogger(__name__)


@router.post("/process-reminders")
@router.get("/process-reminders")  # Vercel Cron issues GET requests by default
def process_reminders(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    # Fail closed. This endpoint delivers every user's due reminders, so left
    # open it is both a way to spam professors' phones and inboxes and a free
    # amplifier for anyone who finds the URL. In development an unset secret
    # is a convenience; in production it is a hole.
    if not settings.CRON_SECRET:
        if settings.ENV == "production":
            logger.error("CRON_SECRET is not set; refusing to run the reminder processor unauthenticated")
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Reminder processing is not configured: set CRON_SECRET on the server.",
            )
        logger.warning("CRON_SECRET is unset; the cron endpoint is unauthenticated (development only)")
    else:
        expected = f"Bearer {settings.CRON_SECRET}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid cron secret")

    return process_due_reminders(db)
