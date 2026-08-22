"""
In-process reminder delivery loop.

On a persistent host (Render, Railway, Fly, a plain VM) the app runs as a
long-lived process, so reminders can be delivered by a background task
instead of an externally-triggered cron endpoint.

This is intentionally opt-in via ENABLE_BACKGROUND_SCHEDULER so it stays
off on serverless platforms like Vercel, where background tasks are killed
between invocations and `/api/cron/process-reminders` is the correct
mechanism instead.

Delivery itself is idempotent (guarded by Reminder.is_sent), so running both
this loop and the cron endpoint would be harmless but redundant.
"""
import asyncio
import logging

from app.config import get_settings
from app.database import SessionLocal
from app.services.reminder_service import process_due_reminders

logger = logging.getLogger(__name__)
settings = get_settings()


async def reminder_loop() -> None:
    interval = max(30, settings.REMINDER_POLL_SECONDS)
    logger.info("Background reminder scheduler started (every %ss)", interval)

    while True:
        try:
            # Run the blocking DB work off the event loop so it can't stall
            # request handling.
            result = await asyncio.to_thread(_process_once)
            if result and result.get("sent"):
                logger.info("Delivered %s reminder(s)", result["sent"])
        except asyncio.CancelledError:
            logger.info("Background reminder scheduler stopping")
            raise
        except Exception:
            # Never let a transient failure (e.g. a dropped DB connection)
            # kill the loop for the lifetime of the process.
            logger.exception("Reminder processing failed; will retry next tick")

        await asyncio.sleep(interval)


def _process_once() -> dict:
    db = SessionLocal()
    try:
        return process_due_reminders(db)
    finally:
        db.close()
