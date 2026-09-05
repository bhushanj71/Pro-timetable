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
from app.database import engine, init_db
from app.routers import (
    ai_agent,
    admin,
    ai,
    analytics,
    auth,
    cron,
    events,
    export,
    google_auth,
    notifications,
    org,
    pages,
    reminders,
    tasks,
    timetable,
    work,
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
    # Not on serverless. A loop started inside a request handler is frozen the
    # moment the response is sent and resumes only when the next request
    # happens to land on the same instance -- so reminders would fire late,
    # early, or never, depending on traffic. The platform's own cron hits
    # /api/cron/process-reminders instead, which is a request and therefore
    # actually runs.
    from app.database import IS_SERVERLESS

    if settings.ENABLE_BACKGROUND_SCHEDULER and not IS_SERVERLESS:
        from app.services.background import reminder_loop

        scheduler_task = asyncio.create_task(reminder_loop())

    yield

    if scheduler_task:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

from app.security_headers import SecurityHeadersMiddleware  # noqa: E402


@app.middleware("http")
async def _replicate_on_serverless(request, call_next):
    """Drive replication from requests where no thread can run.

    Attached to the response as a background task, so the user has their reply
    before any copying starts -- this must never be latency the visitor pays.
    Only on serverless: everywhere else the worker thread does it properly,
    on its own clock, without touching a request at all.
    """
    response = await call_next(request)

    from app.database import IS_SERVERLESS, replicator

    if IS_SERVERLESS and replicator.enabled:
        from starlette.background import BackgroundTask

        existing = response.background
        task = BackgroundTask(replicator.pump)
        if existing is not None:
            from starlette.background import BackgroundTasks

            combined = BackgroundTasks()
            combined.add_task(existing)
            combined.add_task(replicator.pump)
            response.background = combined
        else:
            response.background = task
    return response

app.add_middleware(SecurityHeadersMiddleware)

# Compression before anything else in the chain, so it wraps every response.
# The event list measured 96KB of JSON for a term's schedule; JSON of that
# shape compresses by roughly an order of magnitude, which matters far more
# on a phone than any query tuning.
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1024)

# Timing. The Server-Timing header describes internal structure, so it is
# emitted while developing and withheld in production; the slow-request and
# slow-query logs run everywhere, because that is where they are needed.
from app.perf import PerfMiddleware, install_sql_timing  # noqa: E402

app.add_middleware(PerfMiddleware, expose_header=settings.ENV != "production")


# ---------------------------------------------------------------------------
# Static assets: cached by their own version, not re-asked for every page
# ---------------------------------------------------------------------------
@app.middleware("http")
async def cache_static(request, call_next):
    """Let the browser keep an asset it already has.

    Every stylesheet and script is requested with ?v=<mtime>, so the URL
    changes the moment the file does. That is precisely the case `immutable`
    exists for: the browser may keep it for a year and never ask again,
    because a changed file is a different URL.

    Without this the responses were correct but pointlessly expensive -- an
    ETag and a 304 for each of fourteen assets on every single page load. Zero
    bytes each and a round trip each, which on a phone is the slowest part of
    opening a page that renders in fifteen milliseconds.

    Only versioned requests get the long life. An asset asked for without a ?v
    could be anything, including a URL that stays the same while the file
    changes, so those keep revalidating.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/") and response.status_code < 400:
        if request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=300")
    return response
install_sql_timing(engine)

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
app.include_router(ai_agent.router)
app.include_router(analytics.router)
app.include_router(export.router)
app.include_router(cron.router)
app.include_router(admin.router)
app.include_router(org.router)
app.include_router(notifications.router)
app.include_router(google_auth.router)
app.include_router(work.router)
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


@app.get("/.well-known/assetlinks.json", include_in_schema=False)
def android_asset_links():
    """Proves to Chrome that the Play listing and this site are the same thing.

    Without it a Trusted Web Activity still runs, but with the site's URL
    showing in a bar across the top -- which is how a reviewer, and every
    user, can tell it is a web page in a jacket.

    Served from configuration rather than a checked-in file because the
    fingerprint is per-deployment: Play re-signs uploads with its own key, so
    the value differs between a local build and the store.
    """
    from fastapi.responses import JSONResponse

    package = settings.ANDROID_PACKAGE_NAME.strip()
    prints = [f.strip().upper() for f in settings.ANDROID_SHA256_FINGERPRINTS.split(",") if f.strip()]

    if not package or not prints:
        # 404 rather than an empty list: an empty statement of ownership is a
        # claim that nothing owns this domain, and Chrome caches it.
        return JSONResponse(
            {"detail": "No Android app is configured for this deployment."},
            status_code=404,
        )

    return JSONResponse([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": package,
            "sha256_cert_fingerprints": prints,
        },
    }])


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

    from app.database import CONFIG_ERROR, describe_connection, engine, replicator

    # The engine actually serving traffic, which after a failover is not the
    # primary. Probing the primary here would report "unreachable" while the
    # app was working perfectly on the mirror.
    active = replicator.active_engine if replicator.enabled else engine

    db_ok, db_error = True, None
    try:
        with active.connect() as conn:
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
        "replication": replicator.status(),
        "config_error": CONFIG_ERROR,
    }
    if not db_ok:
        # Only while broken: reveals which host is actually being dialed
        # (password redacted) so the misconfiguration can be pinpointed.
        payload["connecting_to"] = describe_connection()

    # A configured-but-unreachable AI provider degrades silently: every prompt
    # falls back to the rule-based parser, which reads a sentence far less
    # well, and nothing anywhere says why the answers got worse.
    from app.services.ai_service import LAST_PROVIDER_ERROR, get_ai_service

    ai = get_ai_service()
    payload["ai"] = {
        "provider": ai.provider,
        "configured": bool(ai.is_configured),
        "status": (
            "not_configured" if not ai.is_configured
            else "failing" if LAST_PROVIDER_ERROR["error"] else "ok"
        ),
    }
    if LAST_PROVIDER_ERROR["error"]:
        payload["ai"]["last_error"] = LAST_PROVIDER_ERROR["error"]
        payload["ai"]["last_error_at"] = LAST_PROVIDER_ERROR["at"]
        payload["ai"]["effect"] = (
            "Prompts are being parsed by the built-in fallback, which is much "
            "weaker at reading a sentence. Check AI_PROVIDER, AI_API_KEY and the "
            "provider's endpoint."
        )
    return payload

