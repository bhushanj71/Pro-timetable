"""
Performance instrumentation.

Two things, both aimed at finding real slowness rather than guessing at it:
a server-timing log for requests that take too long, and a SQL hook that
reports slow statements and counts queries per request so an N+1 shows up as
a number instead of a hunch.

Nothing here reaches the user. Timings go to the application log, and the
optional Server-Timing header is emitted only outside production, since it
describes internal structure.
"""
import logging
import time
from contextvars import ContextVar

from sqlalchemy import event as sa_event
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("app.perf")

# Requests slower than this are worth a line in the log. Chosen so a healthy
# request never logs: the measured endpoints on this app run 4-10ms.
SLOW_REQUEST_MS = 400
SLOW_QUERY_MS = 120
# An endpoint issuing more queries than this is almost certainly doing one per
# row somewhere.
MANY_QUERIES = 25

# A dict, not two ints.
#
# FastAPI runs sync endpoints in a threadpool, and the worker thread gets a
# *copy* of the context. Rebinding a ContextVar inside that thread therefore
# updates the copy and is invisible to the middleware, which is why the first
# version of this reported "0 queries" for every request. Storing a mutable
# object and mutating it works, because both contexts hold the same reference.
_stats: ContextVar[dict] = ContextVar("perf_stats")


def install_sql_timing(engine) -> None:
    """Time every statement, and flag the slow ones."""

    @sa_event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        context._perf_started = time.perf_counter()

    @sa_event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        started = getattr(context, "_perf_started", None)
        if started is None:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000

        try:
            bucket = _stats.get()
            bucket["count"] += 1
            bucket["ms"] += elapsed_ms
        except LookupError:
            pass  # outside a request (startup, background loop)

        if elapsed_ms >= SLOW_QUERY_MS:
            # One line, first clause only: full statements with bound
            # parameters would put user data in the log.
            logger.warning("Slow query %.0fms: %s", elapsed_ms, " ".join(statement.split())[:140])


class PerfMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, expose_header: bool = False):
        super().__init__(app)
        self.expose_header = expose_header

    async def dispatch(self, request, call_next):
        bucket = {"count": 0, "ms": 0.0}
        _stats.set(bucket)
        started = time.perf_counter()

        response = await call_next(request)

        total_ms = (time.perf_counter() - started) * 1000
        queries = bucket["count"]
        db_ms = bucket["ms"]

        if total_ms >= SLOW_REQUEST_MS or queries >= MANY_QUERIES:
            logger.warning(
                "Slow request %s %s -> %s in %.0fms (%d queries, %.0fms in db)",
                request.method, request.url.path, response.status_code, total_ms, queries, db_ms,
            )

        if self.expose_header:
            # Readable in devtools' Timing tab while developing.
            response.headers["Server-Timing"] = (
                f'db;dur={db_ms:.1f};desc="{queries} queries", total;dur={total_ms:.1f}'
            )
        return response
