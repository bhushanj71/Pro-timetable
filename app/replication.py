"""A live mirror of the database, and automatic failover onto it.

What this is
------------
Every committed change on the active database is copied to a second database
within about a second. If the active one stops answering, the application
switches to the mirror and keeps serving -- reads and writes both -- and copies
back the other way when the first returns.

How it works, and why this shape
--------------------------------
A change is recorded as an *identity*, not a payload: the table and the primary
key of the row that changed, appended to `replication_log` inside the same
transaction as the change itself. That makes the log atomic with the data (a
crash cannot leave one without the other) and durable (a restart resumes where
it stopped, which an in-memory queue cannot).

The worker then reads the current state of that row from the source and writes
it to the destination. Recording identity rather than a payload has three
consequences, all of them good:

  * The log stays small. A 10 MB attachment does not become a 13 MB base64
    log row on top of the 10 MB it already occupies.
  * Replication is convergent. Applying the same entry twice is harmless, and
    a row that changed three times before the worker caught up is copied once,
    at its final value.
  * A row deleted after being logged simply is not there when the worker looks,
    and the deletion propagates. The two databases agree either way.

The destination remembers the last sequence it applied, in its own
`replication_state` row. Nothing is tracked in memory that matters.

What this is not
----------------
This is asynchronous replication, so failover can lose the last second of
writes. Synchronous replication with a guaranteed zero-loss failover is a
database-level feature -- Postgres streaming replication with a coordinator, or
a managed high-availability plan -- and no application-level design can honestly
claim it.

It also assumes one writing process. Two instances that disagreed about which
database is live would write to different ones and diverge, and resolving that
needs a coordinator holding a lease. `REPLICATION_ALLOW_FAILOVER` exists so
failover can be switched off if this app is ever scaled past one instance.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    event,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("app.replication")

# How long the worker sleeps when it finds nothing to do. Short enough that
# "real time" is honest, long enough not to hammer two databases with empty
# polls all day.
IDLE_SECONDS = 1.0

# How often the health monitor pings the database it is not currently using.
# Only relevant after a failover, when it is deciding whether to come back.
PROBE_SECONDS = 15.0

# Consecutive failures before switching. One timeout is a blip; three in a row
# during a request is an outage. Flapping between two databases is worse than
# being down on one.
FAILURES_BEFORE_FAILOVER = 3

_meta = MetaData()

# Deliberately Core tables rather than ORM models. They are infrastructure --
# nothing in the application reads them -- and keeping them off the declarative
# Base means replication cannot recurse into replicating its own log.
replication_log = Table(
    "replication_log", _meta,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("table_name", String(64), nullable=False),
    Column("row_pk", String(64), nullable=False),
    Column("op", String(8), nullable=False),           # upsert | delete
    Column("created_at", DateTime(timezone=True), nullable=False),
)

replication_state = Table(
    "replication_state", _meta,
    Column("id", Integer, primary_key=True),
    Column("last_seq", Integer, nullable=False, default=0),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


# The example URL from the documentation, and the placeholders the various
# providers leave in the strings they hand you. Pasting one of these is a much
# more common mistake than a genuinely malformed URL, and it deserves a better
# answer than a DNS timeout.
_PLACEHOLDERS = (
    "user:pass@host",
    "://user:pass@",
    "/dbname",
    "[YOUR-PASSWORD]",
    "YOUR-PASSWORD",
    "<password>",
    "[PASSWORD]",
    "<username>",
    "<host>",
    "your-project",
)


def placeholder_problem(url: str) -> str | None:
    """A plain explanation if this is an example rather than a real URL."""
    lowered = (url or "").lower()
    for token in _PLACEHOLDERS:
        if token.lower() in lowered:
            return (
                f"MIRROR_DATABASE_URL still contains the example text {token!r}. "
                "Replace the whole value with the connection string of a real "
                "second database. Until then the app runs on the primary alone."
            )
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Replicator:
    """Owns both engines, which one is live, and the copying between them."""

    def __init__(self, primary_engine, primary_url: str, mirror_url: str | None,
                 *, allow_failover: bool = True):
        self.primary = primary_engine
        self.primary_url = primary_url
        self.mirror_url = mirror_url
        self.allow_failover = allow_failover

        self.mirror = None
        self.enabled = False
        self.active_name = "primary"          # primary | mirror
        self.failed_over_at: datetime | None = None
        self.last_error: str | None = None
        self.last_replicated_at: datetime | None = None
        self.pending: int = 0

        self._consecutive_failures = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

        if not mirror_url:
            return

        problem = placeholder_problem(mirror_url)
        if problem:
            # Never fatal, and never a silent no-op either. A URL that is
            # obviously an example should say so on /api/health, rather than
            # spending ten seconds failing to resolve a host called "host" and
            # reporting that as though it were a network fault.
            self.last_error = problem
            logger.error("Mirror not configured: %s", problem)
            return

        try:
            connect_args = ({"check_same_thread": False} if mirror_url.startswith("sqlite")
                            else {"connect_timeout": 10})
            self.mirror = create_engine(mirror_url, connect_args=connect_args, pool_pre_ping=True)
            self.enabled = True
        except Exception as exc:
            # Never fatal. A broken mirror URL must not stop the app booting on
            # a perfectly good primary.
            self.last_error = f"Could not open the mirror: {type(exc).__name__}: {exc}"
            logger.error(self.last_error)

    # -- which engine is live -------------------------------------------
    @property
    def active_engine(self):
        with self._lock:
            if self.active_name == "mirror" and self.mirror is not None:
                return self.mirror
            return self.primary

    @property
    def standby_engine(self):
        with self._lock:
            if self.active_name == "mirror":
                return self.primary
            return self.mirror

    # -- schema ----------------------------------------------------------
    def prepare(self, base_metadata) -> None:
        """Give the mirror the same tables as the primary, plus the two of our
        own. Both databases carry both infrastructure tables because either can
        become the source."""
        if not self.enabled:
            return
        try:
            base_metadata.create_all(bind=self.mirror)
            _meta.create_all(bind=self.mirror)
            _meta.create_all(bind=self.primary)
            for engine in (self.primary, self.mirror):
                with engine.begin() as conn:
                    row = conn.execute(select(replication_state.c.id)).first()
                    if not row:
                        conn.execute(insert(replication_state).values(
                            id=1, last_seq=0, updated_at=_now()))
            self.backfill(base_metadata)
            logger.info("Mirror database ready")
        except Exception as exc:
            self.enabled = False
            self.last_error = f"Could not prepare the mirror: {type(exc).__name__}: {exc}"
            logger.error(self.last_error)

    def backfill(self, base_metadata) -> int:
        """Copy everything across, once, when the mirror is new.

        Without this a mirror added to a database that already has months of
        data would only ever receive changes made from that moment on -- and a
        failover would serve an almost empty database, which is worse than
        having no mirror at all because it looks like it works.

        Runs only when the mirror has applied nothing and holds no rows, so a
        restart cannot trigger a second full copy over live data.
        """
        with self.mirror.begin() as conn:
            applied = conn.execute(select(replication_state.c.last_seq)).scalar() or 0
        if applied:
            return 0

        copied = 0
        # Creation order, so a row never arrives before the row it references.
        for table in base_metadata.sorted_tables:
            with self.primary.connect() as sconn:
                rows = [dict(r) for r in sconn.execute(select(table)).mappings()]
            if not rows:
                continue
            with self.mirror.begin() as dconn:
                if dconn.execute(select(text("count(*)")).select_from(table)).scalar():
                    continue                     # not empty; leave it alone
                dconn.execute(insert(table), rows)
                copied += len(rows)

        # Start from the primary's current position, so the worker does not
        # replay the whole history it has just copied by hand.
        with self.primary.connect() as sconn:
            head = sconn.execute(
                select(replication_log.c.seq).order_by(replication_log.c.seq.desc()).limit(1)
            ).scalar() or 0
        with self.mirror.begin() as dconn:
            dconn.execute(update(replication_state)
                          .where(replication_state.c.id == 1)
                          .values(last_seq=head, updated_at=_now()))
        if copied:
            logger.info("Mirror backfilled with %s existing rows", copied)
        return copied

    # -- recording -------------------------------------------------------
    def record(self, conn, changes: list[tuple[str, str, str]]) -> None:
        """Append change identities inside the caller's own transaction.

        Called from a session event while the transaction is still open, which
        is what makes the log atomic with the data it describes.
        """
        if not self.enabled or not changes:
            return
        stamp = _now()
        conn.execute(insert(replication_log), [
            {"table_name": t, "row_pk": pk, "op": op, "created_at": stamp}
            for t, pk, op in changes
        ])

    # -- copying ---------------------------------------------------------
    def _copy_batch(self, source, dest, source_meta, limit: int = 200) -> int:
        """Apply the next few log entries from source to dest. Returns how many."""
        with dest.begin() as dconn:
            last = dconn.execute(select(replication_state.c.last_seq)).scalar() or 0

        with source.connect() as sconn:
            entries = sconn.execute(
                select(replication_log)
                .where(replication_log.c.seq > last)
                .order_by(replication_log.c.seq)
                .limit(limit)
            ).fetchall()

            if not entries:
                return 0

            # Read every row's current state from the source first, so the
            # destination transaction is short and never waits on the source.
            work = []
            for e in entries:
                table = source_meta.tables.get(e.table_name)
                if table is None:
                    continue                       # a table this build does not know
                pk_col = list(table.primary_key.columns)[0]
                row = sconn.execute(
                    select(table).where(pk_col == e.row_pk)
                ).mappings().first()
                work.append((table, pk_col, e.row_pk, dict(row) if row else None))

        highest = entries[-1].seq
        with dest.begin() as dconn:
            for table, pk_col, pk_value, values in work:
                if values is None:
                    # Gone from the source, whatever the log said. Convergent:
                    # the two databases agree on absence.
                    dconn.execute(delete(table).where(pk_col == pk_value))
                    continue
                touched = dconn.execute(
                    update(table).where(pk_col == pk_value).values(**values)
                ).rowcount
                if not touched:
                    dconn.execute(insert(table).values(**values))
            dconn.execute(update(replication_state)
                          .where(replication_state.c.id == 1)
                          .values(last_seq=highest, updated_at=_now()))
        return len(entries)

    def _pending_count(self, source, dest) -> int:
        try:
            with dest.connect() as dconn:
                last = dconn.execute(select(replication_state.c.last_seq)).scalar() or 0
            with source.connect() as sconn:
                return sconn.execute(
                    select(text("count(*)")).select_from(replication_log)
                    .where(replication_log.c.seq > last)
                ).scalar() or 0
        except Exception:
            return -1

    def sync_once(self) -> int:
        """One pass, source -> destination, in whichever direction is live."""
        if not self.enabled:
            return 0
        from app.database import Base

        with self._lock:
            source = self.active_engine
            dest = self.standby_engine
        if dest is None:
            return 0
        return self._copy_batch(source, dest, Base.metadata)

    # -- failover --------------------------------------------------------
    def note_failure(self, exc: Exception) -> None:
        """Called when a request could not reach the active database."""
        if not (self.enabled and self.allow_failover):
            return
        with self._lock:
            self._consecutive_failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self._consecutive_failures < FAILURES_BEFORE_FAILOVER:
                return
            if self.active_name == "mirror":
                return                              # nowhere left to go
            self._switch_to("mirror")

    def note_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    def _switch_to(self, name: str) -> None:
        previous = self.active_name
        self.active_name = name
        self._consecutive_failures = 0
        self.failed_over_at = _now() if name == "mirror" else None
        logger.error(
            "Database failover: now serving from the %s (was %s). Reason: %s",
            name, previous, self.last_error,
        )

    def try_failback(self) -> bool:
        """Come back to the primary, but only once it has caught up.

        Switching back with the mirror's writes unreplicated would serve a
        database missing everything written during the outage, which is a worse
        failure than the outage was.
        """
        with self._lock:
            if not (self.enabled and self.active_name == "mirror"):
                return False
        try:
            with self.primary.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return False

        from app.database import Base
        try:
            # Drain everything the mirror accumulated while it was live.
            for _ in range(1000):
                if self._copy_batch(self.mirror, self.primary, Base.metadata) == 0:
                    break
            else:
                logger.warning("Failback deferred: the mirror still has a backlog")
                return False
        except Exception as exc:
            logger.error("Failback deferred, could not replay to the primary: %s", exc)
            return False

        with self._lock:
            self.last_error = None
            self._switch_to("primary")
        return True

    # -- worker ----------------------------------------------------------
    def start(self) -> None:
        if not self.enabled or self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="replicator", daemon=True)
        self._worker.start()
        logger.info("Replication worker started")

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        last_probe = 0.0
        while not self._stop.is_set():
            try:
                moved = self.sync_once()
                if moved:
                    self.last_replicated_at = _now()
            except Exception as exc:
                # A mirror that is itself down must never stop the app or the
                # loop. Log once per cycle and keep trying.
                logger.warning("Replication pass failed: %s: %s", type(exc).__name__, exc)
                moved = 0

            now = time.monotonic()
            if now - last_probe > PROBE_SECONDS:
                last_probe = now
                try:
                    self.pending = self._pending_count(self.active_engine, self.standby_engine)
                except Exception:
                    pass
                if self.active_name == "mirror":
                    self.try_failback()

            if not moved:
                self._stop.wait(IDLE_SECONDS)

    # -- serverless -------------------------------------------------------
    _last_pump = 0.0

    def pump(self, min_interval: float = 2.0) -> int:
        """One bounded pass, safe to call from a request.

        This is how replication happens where a background thread cannot run.
        It fits serverless better than it looks: no traffic means no writes,
        so a mirror that only advances while requests are arriving is never
        behind on anything that matters. An idle app has nothing to copy.

        Throttled and bounded, because it runs on somebody's request. It is
        attached to the response as a background task, so the reply has already
        been sent by the time this does anything.
        """
        if not self.enabled:
            return 0
        now = time.monotonic()
        if now - self._last_pump < min_interval:
            return 0
        self._last_pump = now
        try:
            moved = self.sync_once()
            if moved:
                self.last_replicated_at = _now()
            return moved
        except Exception as exc:
            logger.warning("Inline replication pass failed: %s: %s", type(exc).__name__, exc)
            return 0

    # -- reporting -------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "serving_from": self.active_name,
                "failed_over": self.active_name != "primary",
                "failed_over_at": self.failed_over_at.isoformat() if self.failed_over_at else None,
                "last_replicated_at": (
                    self.last_replicated_at.isoformat() if self.last_replicated_at else None
                ),
                "pending_changes": self.pending,
                "last_error": self.last_error,
                "failover_allowed": self.allow_failover,
            }


# --------------------------------------------------------------------------
# Capture: turn ORM flushes into log entries, inside the same transaction
# --------------------------------------------------------------------------
def install_capture(session_factory: sessionmaker, replicator: Replicator) -> None:
    """Record what each transaction changes, inside that transaction.

    Everything happens in one event, and that is the point. `after_flush` is
    the only moment when both things needed are true at once: the change set is
    still populated (session.new / dirty / deleted are cleared once the flush
    completes), and the transaction is still open, so SQL emitted here commits
    or rolls back with the data it describes.

    A first attempt collected here and wrote the log on `before_commit`, which
    does not work: before_commit fires *before* the flush that commit performs,
    so the change set was always empty and nothing was ever logged. Doing both
    in one place removes the ordering question rather than answering it, and
    also removes the need for a rollback hook -- a rolled-back transaction takes
    its own log entries with it.
    """

    @event.listens_for(session_factory, "after_flush")
    def _capture(session: Session, flush_context) -> None:   # noqa: ANN001
        if not replicator.enabled:
            return

        changes: list[tuple[str, str, str]] = []
        for obj in session.deleted:
            entry = _identity(obj, "delete")
            if entry:
                changes.append(entry)
        for obj in list(session.new) + list(session.dirty):
            entry = _identity(obj, "upsert")
            if entry:
                changes.append(entry)
        if not changes:
            return

        try:
            # Core SQL, so this does not itself provoke another flush and
            # cannot recurse into logging its own log.
            replicator.record(session.connection(), changes)
        except Exception as exc:
            # A failure to log must never fail the user's write. The row is
            # still correct in the live database; the mirror picks it up on the
            # next change to that row, or on a full resync.
            logger.warning("Could not record replication entries: %s", exc)


def _identity(obj, op: str) -> tuple[str, str, str] | None:
    """(table, primary key, op) for one ORM object, or None if it has neither.

    Primary keys are populated by the time after_flush runs, including the
    client-side uuid defaults this schema uses, so an entry always names a row
    that exists rather than one the database has not created yet.
    """
    try:
        table = obj.__table__
        pk_col = list(table.primary_key.columns)[0]
        value = getattr(obj, pk_col.name, None)
        if value is None:
            return None
        return (table.name, str(value), op)
    except Exception:
        return None
