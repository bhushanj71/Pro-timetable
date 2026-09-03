"""The live mirror, and failing over onto it.

Two real SQLite files, not mocks. A mock of a database cannot fail the way a
database fails, and the whole point of this module is what happens when one
does.
"""
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import College, OrgStatus, User, normalise_org_name
from app.replication import (
    Replicator,
    install_capture,
    replication_log,
    replication_state,
)


@pytest.fixture
def pair(tmp_path):
    """A primary, a mirror, and a replicator wired between them."""
    primary_url = f"sqlite:///{tmp_path / 'primary.db'}"
    mirror_url = f"sqlite:///{tmp_path / 'mirror.db'}"

    primary = create_engine(primary_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=primary)

    factory = sessionmaker(autocommit=False, autoflush=False, bind=primary)
    rep = Replicator(primary, primary_url, mirror_url)
    install_capture(factory, rep)
    rep.prepare(Base.metadata)

    yield {"rep": rep, "factory": factory, "primary": primary,
           "mirror_url": mirror_url, "primary_url": primary_url}
    rep.stop()


def _college(session, name="Probe College"):
    c = College(name=name, normalised_name=normalise_org_name(name),
                location="Somewhere", status=OrgStatus.ACTIVE.value)
    session.add(c)
    session.commit()
    return c


def _mirror_names(rep):
    with rep.mirror.connect() as conn:
        return {r[0] for r in conn.execute(select(College.__table__.c.name))}


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------
def test_a_write_is_logged_inside_its_own_transaction(pair):
    """Atomic with the data. A log written afterwards can be lost by a crash
    in between, leaving a change nothing will ever replicate."""
    s = pair["factory"]()
    _college(s)
    s.close()

    with pair["primary"].connect() as conn:
        rows = conn.execute(select(replication_log)).fetchall()
    assert [r.table_name for r in rows] == ["colleges"]
    assert rows[0].op == "upsert"


def test_a_rolled_back_write_is_not_logged(pair):
    """The change never happened, so there is nothing to copy. Logging it
    would have the mirror chase a row that does not exist."""
    s = pair["factory"]()
    s.add(College(name="Ghost", normalised_name="ghost", status=OrgStatus.ACTIVE.value))
    s.flush()
    s.rollback()
    s.close()

    with pair["primary"].connect() as conn:
        assert conn.execute(select(replication_log)).fetchall() == []


def test_nothing_is_recorded_when_no_mirror_is_configured(tmp_path):
    """The feature has to be genuinely inert when it is off -- no extra table,
    no extra write on anybody's transaction."""
    url = f"sqlite:///{tmp_path / 'solo.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    rep = Replicator(engine, url, None)
    install_capture(factory, rep)

    assert rep.enabled is False
    s = factory()
    _college(s)
    s.close()
    with engine.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(text("SELECT count(*) FROM replication_log"))


# --------------------------------------------------------------------------
# Copying
# --------------------------------------------------------------------------
def test_a_row_reaches_the_mirror(pair):
    s = pair["factory"]()
    _college(s, "Replicated College")
    s.close()

    assert pair["rep"].sync_once() == 1
    assert "Replicated College" in _mirror_names(pair["rep"])


def test_an_update_reaches_the_mirror(pair):
    s = pair["factory"]()
    c = _college(s, "Before")
    pair["rep"].sync_once()

    c.name = "After"
    s.commit()
    s.close()
    pair["rep"].sync_once()

    names = _mirror_names(pair["rep"])
    assert "After" in names and "Before" not in names


def test_a_delete_reaches_the_mirror(pair):
    s = pair["factory"]()
    c = _college(s, "Doomed")
    pair["rep"].sync_once()
    assert "Doomed" in _mirror_names(pair["rep"])

    s.delete(c)
    s.commit()
    s.close()
    pair["rep"].sync_once()
    assert "Doomed" not in _mirror_names(pair["rep"])


def test_replaying_the_same_entries_twice_changes_nothing(pair):
    """Convergent by design: the worker copies the row's current state, so
    applying an entry again writes the same values."""
    s = pair["factory"]()
    _college(s, "Idempotent")
    s.close()
    pair["rep"].sync_once()

    with pair["rep"].mirror.begin() as conn:
        conn.execute(update_state(0))
    pair["rep"].sync_once()

    with pair["rep"].mirror.connect() as conn:
        assert conn.execute(
            select(text("count(*)")).select_from(College.__table__)
        ).scalar() == 1


def update_state(value):
    from sqlalchemy import update as _update
    return _update(replication_state).where(replication_state.c.id == 1).values(
        last_seq=value, updated_at=datetime.now(timezone.utc))


def test_a_row_changed_three_times_is_copied_once_at_its_final_value(pair):
    """The log names the row, not the values, so a burst of edits collapses to
    one copy of the row as it finally stands."""
    s = pair["factory"]()
    c = _college(s, "v1")
    for name in ("v2", "v3", "v4"):
        c.name = name
        s.commit()
    s.close()

    pair["rep"].sync_once()
    assert _mirror_names(pair["rep"]) == {"v4"}


def test_the_mirror_remembers_where_it_got_to(pair):
    s = pair["factory"]()
    _college(s, "One")
    s.close()
    pair["rep"].sync_once()

    with pair["rep"].mirror.connect() as conn:
        first = conn.execute(select(replication_state.c.last_seq)).scalar()
    assert first > 0

    # Nothing new: a second pass must do no work rather than start over.
    assert pair["rep"].sync_once() == 0


def test_replication_resumes_from_the_log_after_a_restart(pair):
    """The queue is a table, not a variable, so a process that dies with work
    outstanding picks it up again from the destination's own bookmark.

    The mirror is deliberately past its first sync here: a fresh mirror would
    be filled by the backfill instead, which would prove the wrong thing.
    """
    s = pair["factory"]()
    _college(s, "Before the restart")
    s.close()
    pair["rep"].sync_once()                      # mirror is now past seq 0

    s = pair["factory"]()
    _college(s, "Written while the worker was dead")
    s.close()

    # A brand-new replicator over the same two files, as a restart would be.
    fresh = Replicator(pair["primary"], pair["primary_url"], pair["mirror_url"])
    fresh.prepare(Base.metadata)
    assert fresh.sync_once() == 1, "should replay from the log, not backfill"
    assert "Written while the worker was dead" in _mirror_names(fresh)
    fresh.stop()


# --------------------------------------------------------------------------
# Attaching a mirror to a database that already has data
# --------------------------------------------------------------------------
def test_a_new_mirror_is_backfilled_with_what_is_already_there(tmp_path):
    """The real case: months of data already in the primary, a mirror added
    today. Without this the mirror would only ever hold changes made from now
    on, and a failover would serve an almost empty database -- worse than no
    mirror, because it looks like it works."""
    primary_url = f"sqlite:///{tmp_path / 'p.db'}"
    engine = create_engine(primary_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)

    s = sessionmaker(bind=engine)()
    _college(s, "Written long before the mirror existed")
    s.close()

    rep = Replicator(engine, primary_url, f"sqlite:///{tmp_path / 'm.db'}")
    rep.prepare(Base.metadata)
    assert "Written long before the mirror existed" in _mirror_names(rep)
    rep.stop()


def test_backfill_does_not_run_twice_over_live_data(pair):
    """A restart must not re-copy the world on top of a mirror that has been
    taking writes."""
    s = pair["factory"]()
    _college(s, "One")
    s.close()
    pair["rep"].sync_once()

    assert pair["rep"].backfill(Base.metadata) == 0
    assert _mirror_names(pair["rep"]) == {"One"}


def test_seeding_reaches_the_mirror(tmp_path, monkeypatch):
    """The log table has to exist before anything writes rows, or the seed --
    the default college and its eight departments -- is written to the primary
    and silently never replicated."""
    from sqlalchemy import func

    from app.models import Department

    primary_url = f"sqlite:///{tmp_path / 'p.db'}"
    engine = create_engine(primary_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)

    rep = Replicator(engine, primary_url, f"sqlite:///{tmp_path / 'm.db'}")
    rep.prepare(Base.metadata)                     # before any seeding

    factory = sessionmaker(bind=engine)
    install_capture(factory, rep)
    s = factory()
    _college(s, "Seeded after prepare")
    s.close()

    assert rep.sync_once() >= 1
    assert "Seeded after prepare" in _mirror_names(rep)
    rep.stop()


# --------------------------------------------------------------------------
# Failover
# --------------------------------------------------------------------------
def test_one_failure_does_not_move_the_database(pair):
    """A single timeout is a blip. Flapping between two databases is worse
    than a moment of slowness on one."""
    pair["rep"].note_failure(RuntimeError("blip"))
    assert pair["rep"].active_name == "primary"


def test_repeated_failures_switch_to_the_mirror(pair):
    for _ in range(3):
        pair["rep"].note_failure(RuntimeError("gone"))
    assert pair["rep"].active_name == "mirror"
    assert pair["rep"].status()["failed_over"] is True
    assert pair["rep"].status()["failed_over_at"] is not None


def test_a_success_resets_the_count(pair):
    """Two failures and a success is a flaky connection, not an outage."""
    pair["rep"].note_failure(RuntimeError("one"))
    pair["rep"].note_failure(RuntimeError("two"))
    pair["rep"].note_success()
    pair["rep"].note_failure(RuntimeError("three"))
    assert pair["rep"].active_name == "primary"


def test_failover_can_be_switched_off(pair):
    """For more than one instance, where two processes could disagree about
    which database is live and write to different ones."""
    pair["rep"].allow_failover = False
    for _ in range(5):
        pair["rep"].note_failure(RuntimeError("gone"))
    assert pair["rep"].active_name == "primary"


def test_writes_continue_on_the_mirror_after_failover(pair):
    """The point of the whole exercise."""
    rep = pair["rep"]
    for _ in range(3):
        rep.note_failure(RuntimeError("primary down"))
    assert rep.active_name == "mirror"

    s = sessionmaker(bind=rep.active_engine)()
    _college(s, "Written during the outage")
    s.close()

    with rep.mirror.connect() as conn:
        names = {r[0] for r in conn.execute(select(College.__table__.c.name))}
    assert "Written during the outage" in names


def test_failing_back_replays_what_the_mirror_took(pair):
    """Switching back without replaying would serve a database missing every
    write made during the outage -- a worse failure than the outage."""
    rep = pair["rep"]
    for _ in range(3):
        rep.note_failure(RuntimeError("primary down"))

    factory = sessionmaker(bind=rep.active_engine)
    install_capture(factory, rep)
    s = factory()
    _college(s, "Made while failed over")
    s.close()

    assert rep.try_failback() is True
    assert rep.active_name == "primary"

    with rep.primary.connect() as conn:
        names = {r[0] for r in conn.execute(select(College.__table__.c.name))}
    assert "Made while failed over" in names


def test_failback_refuses_while_the_primary_is_unreachable(pair):
    rep = pair["rep"]
    for _ in range(3):
        rep.note_failure(RuntimeError("down"))
    rep.primary.dispose()

    class Dead:
        def connect(self):
            raise RuntimeError("still down")

    rep.primary = Dead()
    assert rep.try_failback() is False
    assert rep.active_name == "mirror"


# --------------------------------------------------------------------------
# Not making things worse
# --------------------------------------------------------------------------
def test_a_broken_mirror_url_leaves_the_app_working(tmp_path):
    """A redundancy feature that can take the app down is a liability."""
    url = f"sqlite:///{tmp_path / 'p.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    rep = Replicator(engine, url, "not-a-database-url://nonsense")
    assert rep.enabled is False
    assert rep.active_engine is engine


def test_a_dead_mirror_does_not_fail_a_write(pair):
    """Replication is asynchronous precisely so this is true. The user's write
    succeeds; the copy catches up later."""
    rep = pair["rep"]
    rep.mirror.dispose()

    class Dead:
        def connect(self):
            raise RuntimeError("mirror gone")

        def begin(self):
            raise RuntimeError("mirror gone")

    rep.mirror = Dead()
    s = pair["factory"]()
    _college(s, "Written anyway")          # must not raise
    s.close()

    with pair["primary"].connect() as conn:
        names = {r[0] for r in conn.execute(select(College.__table__.c.name))}
    assert "Written anyway" in names


def test_the_worker_thread_survives_a_failing_pass(pair):
    """A mirror that is itself down must not kill the loop that would notice
    it coming back."""
    rep = pair["rep"]

    class Flaky:
        calls = 0

        def connect(self):
            Flaky.calls += 1
            raise RuntimeError("nope")

        def begin(self):
            raise RuntimeError("nope")

    rep.mirror = Flaky()
    rep.start()
    threading.Event().wait(2.5)
    assert rep._worker.is_alive()
    assert Flaky.calls > 0
    rep.stop()


def test_status_reports_enough_to_diagnose_it(pair):
    keys = set(pair["rep"].status())
    assert {"enabled", "serving_from", "failed_over", "failed_over_at",
            "last_replicated_at", "pending_changes", "last_error",
            "failover_allowed"} <= keys
