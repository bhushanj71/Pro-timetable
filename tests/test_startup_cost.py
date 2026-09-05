"""Startup must be cheap when there is nothing to do, and correct when there is.

The full pass -- create_all plus a reflection of every table -- costs 33 round
trips whether or not anything needs changing. A long-running host pays that
once; a serverless one pays it on every cold start, which at a remote
database's round-trip times is seconds on somebody's first request.

The risk in optimising it is the obvious one: skipping work that actually
needed doing leaves a database missing columns the models select on every
request. These tests are about that risk, not about the speed.

They run against the suite's own database rather than a reloaded module.
Reloading app.database builds a fresh declarative Base whose metadata is empty,
because app.models is already imported and still bound to the old one -- so the
"fresh database" it produced had no tables at all and every measurement from it
was meaningless.
"""
import pytest
from sqlalchemy import Column, String, event, text

from app import database as db


def count_statements(fn) -> int:
    n = {"c": 0}

    @event.listens_for(db.engine, "before_cursor_execute")
    def _tick(*args, **kwargs):          # noqa: ANN001
        n["c"] += 1

    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _tick)
    return n["c"]


@pytest.fixture
def prepared():
    """A database that init_db has already finished with."""
    db.init_db()
    yield
    # Leave the state consistent for whatever runs next.
    db._write_state(db.schema_fingerprint(), True)


# --------------------------------------------------------------------------
# Cheap when it can be
# --------------------------------------------------------------------------
def test_a_prepared_database_costs_one_query_to_start(prepared):
    assert count_statements(db.init_db) <= 2, (
        "startup should read the fingerprint and stop, not reflect every table"
    )


# --------------------------------------------------------------------------
# Correct when it must be
# --------------------------------------------------------------------------
def test_a_fingerprint_from_an_older_build_makes_it_run_again(prepared):
    """The safety property. A fingerprint that did not move with the schema
    would skip a migration and leave the database missing a column that every
    request selects."""
    db._write_state("a-fingerprint-from-an-older-build", True)
    assert count_statements(db.init_db) > 20


def test_an_unseeded_database_runs_the_full_pass(prepared):
    """Matching fingerprint but never seeded: the schema is right and the
    default college is not there, which is not a state to serve from."""
    db._write_state(db.schema_fingerprint(), False)
    assert count_statements(db.init_db) > 20


def test_the_fingerprint_moves_when_a_column_is_added():
    before = db.schema_fingerprint()
    table = db.Base.metadata.tables["colleges"]
    table.append_column(Column("_probe_column", String(8)))
    try:
        assert db.schema_fingerprint() != before
    finally:
        table._columns.remove(table.c._probe_column)
    assert db.schema_fingerprint() == before


def test_the_fingerprint_moves_when_a_migration_is_registered():
    before = db.schema_fingerprint()
    db._ADDITIVE_COLUMNS.setdefault("users", []).append(("_probe", "VARCHAR(8)"))
    try:
        assert db.schema_fingerprint() != before
    finally:
        db._ADDITIVE_COLUMNS["users"] = [
            c for c in db._ADDITIVE_COLUMNS["users"] if c[0] != "_probe"
        ]
    assert db.schema_fingerprint() == before


def test_an_unfinished_startup_is_not_recorded_as_done(monkeypatch):
    """Recording the fingerprint before the seed finished would have the next
    start trust a schema that was never completed."""
    db._write_state("deliberately-stale", False)

    def boom():
        raise RuntimeError("seeding failed")

    monkeypatch.setattr(db, "seed_organisation", boom)
    with pytest.raises(RuntimeError):
        db.init_db()

    fingerprint, seeded = db._read_state()
    assert not (fingerprint == db.schema_fingerprint() and seeded)


def test_reading_the_state_never_raises_on_a_database_without_the_table():
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_schema_state"))
    assert db._read_state() == (None, False)


# --------------------------------------------------------------------------
# Serverless
# --------------------------------------------------------------------------
def test_replication_does_not_start_a_thread_on_serverless(monkeypatch):
    """A thread there is frozen between invocations and holds a connection
    while it sleeps: worse than useless."""
    started = {"n": 0}
    monkeypatch.setattr(db.replicator, "start", lambda: started.__setitem__("n", 1))

    monkeypatch.setattr(db, "IS_SERVERLESS", True)
    db._start_replication()
    assert started["n"] == 0

    monkeypatch.setattr(db, "IS_SERVERLESS", False)
    db._start_replication()
    assert started["n"] == 1


# ---------------------------------------------------------------------------
# Delivery: an asset the browser already has should not be asked for again
# ---------------------------------------------------------------------------
def test_a_versioned_asset_is_kept_rather_than_revalidated(client):
    """Every stylesheet and script is requested with ?v=<mtime>, so the URL
    changes the moment the file does -- which is exactly what `immutable` is
    for. Without it the responses were correct but pointlessly expensive: an
    ETag and a 304 for each of fourteen assets on every page load. Zero bytes
    each and a round trip each, on a page that renders in fifteen
    milliseconds."""
    r = client.get("/static/css/style.css?v=123")
    assert r.status_code == 200
    cache = r.headers.get("cache-control", "")
    assert "immutable" in cache and "max-age=31536000" in cache


def test_an_unversioned_asset_keeps_revalidating(client):
    """It could be a URL that stays the same while the file behind it changes,
    and a year-long cache on one of those is unfixable from the server."""
    r = client.get("/static/css/style.css")
    assert r.status_code == 200
    assert "immutable" not in r.headers.get("cache-control", "")


def test_pages_are_never_cached_that_way(client):
    """A page carries the asset version numbers. Cache the page and the browser
    goes on asking for last week's assets by name."""
    assert "immutable" not in client.get("/login").headers.get("cache-control", "")
