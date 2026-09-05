"""One reminder, one delivery, however many workers are running.

is_sent is set *after* a message goes out, so it can tell you a reminder has
already been delivered but cannot stop a delivery that is already under way.
Two workers that read the queue in the same second therefore both send, and
the professor gets the same reminder twice.

That is usually described as a problem you acquire when you add a second
instance. It was not: process_due_reminders is also called from the
notification endpoint to flush a professor's overdue reminders, so two open
tabs belonging to one person were already enough.

The fix is a claim -- a conditional UPDATE that the database adjudicates --
taken and committed before anything is sent.
"""
from datetime import datetime, timedelta, timezone

from app.models import Reminder, User
from app.services.reminder_service import CLAIM_LEASE_SECONDS, _claim
from tests.conftest import TestingSessionLocal
from tests.test_work_mode import _user, owner  # noqa: F401

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _due_reminder(db, user_id, title="Lecture at two"):
    r = Reminder(
        user_id=user_id,
        title=title,
        reminder_datetime=NOW - timedelta(minutes=5),
        reminder_type="in_app",
        is_sent=False,
    )
    db.add(r)
    db.commit()
    return r


def _me(db):
    return db.query(User).filter(User.email == "w_owner@example.com").one()


def _due_list(db):
    return db.query(Reminder).filter(Reminder.is_sent.is_(False)).all()


def test_two_workers_reading_the_same_queue_do_not_both_send(owner, db_session):
    """The race, written down.

    Both workers have already read the row -- that is the situation the claim
    exists for. Only one of them may come away with it.
    """
    _due_reminder(db_session, _me(db_session).id)
    seen_by_both = _due_list(db_session)
    assert len(seen_by_both) == 1

    first = _claim(db_session, seen_by_both, NOW)
    second = _claim(db_session, seen_by_both, NOW)

    assert len(first) == 1, "somebody has to deliver it"
    assert second == [], "and only one of them"


def test_the_claim_is_visible_to_the_other_worker_before_anything_is_sent(owner, db_session):
    """An uncommitted claim is not a claim.

    The other worker is another process with its own transaction; it cannot
    see a row this one has only written in memory. If the commit moved to
    after the sending loop, both would still send and every other test here
    would still pass.
    """
    _due_reminder(db_session, _me(db_session).id)
    _claim(db_session, _due_list(db_session), NOW)

    elsewhere = TestingSessionLocal()
    try:
        row = elsewhere.query(Reminder).one()
        assert row.claimed_at is not None, "the claim must be committed, not pending"
    finally:
        elsewhere.close()


def test_a_claim_still_inside_its_lease_is_not_taken(owner, db_session):
    _due_reminder(db_session, _me(db_session).id)
    due = _due_list(db_session)
    _claim(db_session, due, NOW)

    later = NOW + timedelta(seconds=CLAIM_LEASE_SECONDS - 30)
    assert _claim(db_session, due, later) == []


def test_a_claim_left_by_a_dead_worker_expires(owner, db_session):
    """Never delivered is worse than delivered twice. A worker killed between
    claiming and sending must not strand the reminder for good."""
    _due_reminder(db_session, _me(db_session).id)
    due = _due_list(db_session)
    _claim(db_session, due, NOW)

    later = NOW + timedelta(seconds=CLAIM_LEASE_SECONDS + 1)
    assert len(_claim(db_session, due, later)) == 1


def test_an_already_delivered_reminder_is_never_claimed(owner, db_session):
    r = _due_reminder(db_session, _me(db_session).id)
    r.is_sent = True
    db_session.commit()

    assert _claim(db_session, [r], NOW) == []


def test_claiming_nothing_is_not_an_error(owner, db_session):
    assert _claim(db_session, [], NOW) == []


def test_each_of_several_due_reminders_is_claimed_once(owner, db_session):
    me = _me(db_session).id
    for i in range(5):
        _due_reminder(db_session, me, title=f"Reminder {i}")

    due = _due_list(db_session)
    first = _claim(db_session, due, NOW)
    second = _claim(db_session, due, NOW)

    assert len(first) == 5
    assert second == []
    assert len({r.id for r in first}) == 5, "no reminder claimed twice"


def test_delivery_still_works(owner, db_session):
    """The guard is worthless if it stops the reminder going out at all."""
    from app.services.reminder_service import process_due_reminders

    _due_reminder(db_session, _me(db_session).id)
    result = process_due_reminders(db_session, now=NOW)

    assert result["processed"] == 1
    assert result["sent"] == 1
    db_session.expire_all()
    assert db_session.query(Reminder).one().is_sent is True


def test_a_reminder_another_worker_holds_is_left_alone(owner, db_session):
    """The integration point.

    Every test above proves the claim works when it is asked for. None of them
    proves the delivery path asks: deleting the call from process_due_reminders
    left all of them green.
    """
    from app.services.reminder_service import process_due_reminders

    r = _due_reminder(db_session, _me(db_session).id)
    _claim(db_session, [r], NOW)          # another worker got there first

    result = process_due_reminders(db_session, now=NOW)

    assert result["processed"] == 0, "it belongs to somebody else"
    assert result["sent"] == 0
    db_session.expire_all()
    assert db_session.query(Reminder).one().is_sent is False


def test_the_lease_is_short_enough_to_be_worth_having():
    """The expiry test above derives its clock from this constant, so it moves
    with it: setting the lease to a year keeps that test green while making the
    promise it tests -- that a stranded reminder comes back -- unkeepable.

    A reminder is a thing with a time on it. Recovering it an hour late is not
    recovering it.
    """
    assert 60 <= CLAIM_LEASE_SECONDS <= 1800, (
        f"a {CLAIM_LEASE_SECONDS}s lease is not a lease"
    )
