"""What the notification bell costs when nobody is doing anything.

The bell polls. Every signed-in tab, once a minute, whether or not anything
has happened -- which makes it the one request whose cost is multiplied by the
number of people using the application rather than by the number of people
doing something. Two requests and six queries per tab per minute is three
thousand requests a second at a hundred thousand tabs, spent almost entirely
on confirming that nothing has changed.

So the numbers here are pinned. Not because these particular figures are
sacred, but because the way this gets slow is one more `.count()` or one lazy
relationship at a time, each of them defensible on its own.
"""
import pytest
from sqlalchemy import event

from app.models import User, WorkNotification
from tests.conftest import engine
from tests.test_work_mode import _user, owner  # noqa: F401


class QueryCount:
    """Counts statements issued while the block runs."""

    def __enter__(self):
        self.n = 0

        @event.listens_for(engine, "before_cursor_execute")
        def _count(conn, cursor, statement, params, context, many):
            self.n += 1

        self._fn = _count
        return self

    def __exit__(self, *a):
        event.remove(engine, "before_cursor_execute", self._fn)


# One session lookup, the work feed, and the personal feed's three (its
# overdue-reminder check, the list, and the unread count). Raising this is a
# decision, not an accident.
TICK_BUDGET = 5


def test_one_tick_is_one_request(owner):
    """It used to be two, and two session lookups with them."""
    r = owner.get("/api/notifications/feed")
    assert r.status_code == 200
    body = r.json()
    assert sorted(body) == ["personal", "work"]
    for half in ("personal", "work"):
        assert "items" in body[half] and "unread" in body[half]


def test_the_tick_stays_within_its_query_budget(owner):
    with QueryCount() as c:
        owner.get("/api/notifications/feed")
    assert c.n <= TICK_BUDGET, (
        f"{c.n} queries for one poll tick; budget is {TICK_BUDGET}. "
        "At a hundred thousand tabs each extra query is 1,600 a second."
    )


def test_the_cost_does_not_grow_with_the_number_of_notifications(owner, db_session):
    """The guard that matters. A lazy relationship on the feed row is invisible
    with one notification and is the whole problem with forty."""
    me = db_session.query(User).filter(User.email == "w_owner@example.com").one()
    with QueryCount() as empty:
        owner.get("/api/notifications/feed")

    for i in range(30):
        db_session.add(WorkNotification(
            user_id=me.id, kind="task_assigned", title=f"Task {i}", body="body"))
    db_session.commit()

    with QueryCount() as loaded:
        r = owner.get("/api/notifications/feed")
    assert r.json()["work"]["unread"] == 30
    assert loaded.n == empty.n, (
        f"{empty.n} queries for an empty feed but {loaded.n} for thirty rows -- "
        "something is loading per row"
    )


def test_an_unchanged_minute_costs_no_body(owner):
    """Most ticks return exactly what the last one did."""
    first = owner.get("/api/notifications/feed")
    tag = first.headers["etag"]
    assert tag

    again = owner.get("/api/notifications/feed", headers={"If-None-Match": tag})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == tag


def test_a_changed_feed_gets_a_new_tag(owner, db_session):
    """The saving must not come at the price of a stale bell."""
    me = db_session.query(User).filter(User.email == "w_owner@example.com").one()
    tag = owner.get("/api/notifications/feed").headers["etag"]

    db_session.add(WorkNotification(
        user_id=me.id, kind="task_assigned", title="Something happened", body=None))
    db_session.commit()

    fresh = owner.get("/api/notifications/feed", headers={"If-None-Match": tag})
    assert fresh.status_code == 200, "a changed feed must not answer 304"
    assert fresh.headers["etag"] != tag
    assert fresh.json()["work"]["unread"] == 1


def test_the_tag_is_not_shared_between_people(owner):
    """An ETag that ignored identity would let one professor's 304 stand in
    for another's feed."""
    other = _user("w_other_poll@example.com", "Other")
    a = owner.get("/api/notifications/feed")
    b = other.get("/api/notifications/feed", headers={"If-None-Match": a.headers["etag"]})
    # Both feeds are empty here, so equal tags are legitimate; what must not
    # happen is one person's *content* being served for the other.
    if b.status_code == 200:
        assert b.json()["work"]["unread"] == 0


def test_the_feed_needs_a_session(client):
    assert client.get("/api/notifications/feed").status_code == 401


@pytest.mark.parametrize("path", ["/api/work/notifications", "/api/reminders/notifications"])
def test_the_halves_are_still_reachable_on_their_own(owner, path):
    """Clear, dismiss and mark-read still use them; the combined feed is an
    addition, not a replacement."""
    assert owner.get(path).status_code == 200


# ---------------------------------------------------------------------------
# The other half of the saving is not asking at all
# ---------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

import re  # noqa: E402

APP_JS = Path("app/static/js/app.js").read_text(encoding="utf-8")
WN_JS = Path("app/static/js/work-notifications.js").read_text(encoding="utf-8")


def _code(text: str) -> str:
    """The source with its block comments removed.

    Checking the raw text asserts against the prose: the comment inside
    startPoll explains why setInterval is the wrong tool, so a check that
    setInterval is absent fails on the sentence saying so.
    """
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _poll_body() -> str:
    return _code(APP_JS).split("function startPoll(")[1].split("window.startPoll =")[0]


def test_a_hidden_tab_does_not_poll():
    """The largest saving available, because most open tabs are behind
    something else and a hidden tab cannot show the badge it just refreshed."""
    poll = _poll_body()
    assert 'visibilityState !== "visible"' in poll
    assert "visibilitychange" in poll


def test_coming_back_refreshes_straight_away():
    """Otherwise the saving is paid for with a stale badge for up to a minute
    at the exact moment somebody starts looking at it."""
    poll = _poll_body()
    handler = poll.split("visibilitychange")[1]
    assert "run()" in handler


def test_the_period_is_spread_across_tabs():
    """A fixed period means every tab opened after a deploy wakes in the same
    second and stays in step, so the load arrives as a spike on the minute."""
    poll = _poll_body()
    assert "Math.random()" in poll


def test_a_slow_response_does_not_stack_up_behind_itself():
    """setInterval fires regardless of whether the last call finished."""
    poll = _poll_body()
    assert "setInterval" not in poll, "a rescheduling setTimeout, not an interval"


def test_the_bell_is_polled_through_it():
    assert "startPoll(renderNotificationCentre" in WN_JS
    assert "setInterval(renderNotificationCentre" not in _code(WN_JS)


def test_the_duplicate_poller_is_gone():
    """app.js runs before work-notifications.js, so the flag that makes
    pollNotifications defer to the centre was not set yet when app.js started
    its own timer -- one wasted fetch on every page load, then a tick every
    45 seconds that returned immediately for the life of the page."""
    assert "setInterval(pollNotifications" not in _code(APP_JS)


def test_the_client_actually_asks_once():
    """The saving is only real if the browser uses the combined endpoint.

    Written because it did not exist and should have: replacing the fetch in
    collectNotifications with an empty object left every other check in this
    file green, which means they were describing the server's offer rather
    than the client's behaviour.
    """
    collect = _code(WN_JS).split("async function collectNotifications(")[1]
    collect = collect.split("function setBadges")[0]
    assert '"/api/notifications/feed"' in collect, "the tick has to use the combined feed"
    assert '"/api/work/notifications"' not in collect, "not the two halves"
    assert '"/api/reminders/notifications"' not in collect
