"""
Endpoint invoked periodically by Vercel Cron (see vercel.json) to process
due reminders. Protected by a shared secret since it has no user session.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.reminder_service import process_due_reminders

router = APIRouter(prefix="/api/cron", tags=["cron"])
settings = get_settings()


@router.post("/process-reminders")
@router.get("/process-reminders")  # Vercel Cron issues GET requests by default
def process_reminders(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if settings.CRON_SECRET:
        expected = f"Bearer {settings.CRON_SECRET}"
        if authorization != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid cron secret")

    return process_due_reminders(db)
