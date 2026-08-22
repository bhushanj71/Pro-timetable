"""
FastAPI application factory. Mounted by api/index.py for Vercel's Python
serverless runtime, and runnable directly with `uvicorn app.main:app` for
local development.
"""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.routers import (
    admin,
    ai,
    analytics,
    auth,
    cron,
    events,
    export,
    pages,
    reminders,
    tasks,
    timetable,
)

settings = get_settings()

# Without this, application loggers inherit the root WARNING level and our
# INFO diagnostics (AI fallbacks, reminder delivery) never reach the host's
# log stream — which is the only way to debug a deployed instance.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    scheduler_task = None
    if settings.ENABLE_BACKGROUND_SCHEDULER:
        from app.services.background import reminder_loop

        scheduler_task = asyncio.create_task(reminder_loop())

    yield

    if scheduler_task:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# Resolve asset paths relative to this file rather than the process working
# directory, so the app starts identically under uvicorn, Render, or Vercel
# regardless of where it was launched from.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(events.router)
app.include_router(reminders.router)
app.include_router(tasks.router)
app.include_router(timetable.router)
app.include_router(ai.router)
app.include_router(analytics.router)
app.include_router(export.router)
app.include_router(cron.router)
app.include_router(admin.router)
app.include_router(pages.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": "Invalid request data"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internal error details (stack traces, DB errors, API keys) to the client.
    return JSONResponse(status_code=500, content={"detail": "An unexpected error occurred. Please try again."})


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}
