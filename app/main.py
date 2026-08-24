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
    google_auth,
    notifications,
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


logger = logging.getLogger(__name__)

# Set when startup DB initialization fails, so /api/health can report *why*
# instead of the platform showing an opaque failed deploy.
_startup_error: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _startup_error

    try:
        init_db()

        # Provision the first admin if the environment asks for one. Kept
        # inside the same guard: a bootstrap failure must not stop the app.
        from app.database import SessionLocal
        from app.services.bootstrap import bootstrap_admin

        _db = SessionLocal()
        try:
            bootstrap_admin(_db)
        finally:
            _db.close()
    except Exception as exc:
        # Deliberately non-fatal. If the database is unreachable, letting the
        # process die makes the host report only "deploy failed" with the real
        # cause buried. Starting anyway keeps /api/health reachable so the
        # actual error is visible, and the DB is retried on the next boot.
        _startup_error = f"{type(exc).__name__}: {exc}"
        logger.error("Database initialization failed at startup: %s", _startup_error)
        logger.error(
            "If using Supabase, note the direct db.<ref>.supabase.co:5432 host is "
            "IPv6-only and unreachable from IPv4-only platforms such as Render. "
            "Use the connection pooler host (aws-<region>.pooler.supabase.com) instead."
        )

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

from app.security_headers import SecurityHeadersMiddleware  # noqa: E402

app.add_middleware(SecurityHeadersMiddleware)

# Resolve asset paths relative to this file rather than the process working
# directory, so the app starts identically under uvicorn, Render, or Vercel
# regardless of where it was launched from.
_STATIC_DIR = Path(__file__).resolve().parent / "static"

# StaticFiles raises at construction when the directory is missing, and this
# runs at import — so on a host that did not bundle app/static (a serverless
# build that only packages *.py, say) the whole application dies before a
# single route is registered, and every request returns an opaque 500 with no
# way to find out why. Degrade instead: skip the mount, record the reason, and
# let /api/health report it.
_assets_error: str | None = None
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:
    _assets_error = (
        f"Static assets are missing from the deployment ({_STATIC_DIR} does not exist). "
        "The API still works, but pages will render unstyled."
    )
    logger.error(_assets_error)

# Cache-busting token for static assets. Browsers otherwise keep serving a
# cached app.js/style.css after a deploy, so users run old JavaScript against
# a new API. Derived from the newest static file's mtime, so it changes
# exactly when the assets do.
def _asset_version() -> str:
    try:
        newest = max(f.stat().st_mtime for f in _STATIC_DIR.rglob("*") if f.is_file())
        return str(int(newest))
    except ValueError:
        return "0"


ASSET_VERSION = _asset_version()

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
app.include_router(notifications.router)
app.include_router(google_auth.router)
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


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Served from the root so the worker's scope covers the whole site —
    a worker at /static/js/sw.js could only control /static/js/*."""
    from fastapi.responses import FileResponse

    return FileResponse(
        _STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.json", include_in_schema=False)
def web_manifest():
    from fastapi.responses import FileResponse

    return FileResponse(_STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/api/health")
def health():
    """Liveness probe that also surfaces database reachability.

    Returns 200 even when the database is down so the platform keeps the
    service routable and the payload can explain what is actually broken —
    a health check that just fails gives you nothing to debug with.
    """
    from sqlalchemy import text

    from app.database import CONFIG_ERROR, describe_connection, engine

    db_ok, db_error = True, None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_error = f"{type(exc).__name__}: {exc}"[:300]

    payload = {
        "status": "ok" if db_ok and not _startup_error and not CONFIG_ERROR else "degraded",
        "app": settings.APP_NAME,
        "database": "connected" if db_ok else "unreachable",
        "database_error": db_error,
        "startup_error": _startup_error,
        "assets_error": _assets_error,
        "config_error": CONFIG_ERROR,
    }
    if not db_ok:
        # Only while broken: reveals which host is actually being dialed
        # (password redacted) so the misconfiguration can be pinpointed.
        payload["connecting_to"] = describe_connection()
    return payload
