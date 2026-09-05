"""What is behind a headline count.

The three tiles on the Work dashboard printed a number and stopped there --
two active, and no way to ask which two. Pressing one now opens the set the
number was drawn from.

The property that matters is that it *is* that set. A history that disagrees
with the tile above it is worse than no history: the reader has no way to tell
which of the two is lying, so both stop being worth reading. Every test here
puts real work in each state and checks the two against each other.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_work_mode import _community, _join, _members, _user, owner, rahul  # noqa: F401


def _assigned(owner_c, community_id, member_id, title, **extra):
    body = {"title": title, "assignee_ids": [member_id], **extra}
    r = owner_c.post(f"/api/work/communities/{community_id}/tasks", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture
def workload(owner, rahul):
    """One of each: a request unanswered, work accepted, work finished."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])

    waiting = _assigned(owner, c["id"], ids["Rahul"], "Unanswered request")

    running = _assigned(owner, c["id"], ids["Rahul"], "Work in progress")
    rahul.post(f"/api/work/tasks/{running['id']}/respond", json={"accept": True})

    finished = _assigned(owner, c["id"], ids["Rahul"], "Finished work")
    rahul.post(f"/api/work/tasks/{finished['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{finished['id']}/progress", json={"progress": 100})

    return {"client": rahul, "community": c,
            "waiting": waiting, "running": running, "finished": finished}


def _history(client, bucket):
    r = client.get(f"/api/work/history/{bucket}")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("bucket,expected", [
    ("pending", "Unanswered request"),
    ("active", "Work in progress"),
    ("completed", "Finished work"),
])
def test_each_count_opens_the_work_it_counted(workload, bucket, expected):
    body = _history(workload["client"], bucket)
    assert [i["title"] for i in body["items"]] == [expected]


@pytest.mark.parametrize("bucket", ["active", "pending", "completed"])
def test_the_history_agrees_with_the_number_above_it(workload, bucket):
    """The one thing that must never drift. Both are read from the same
    assignments, and this is what keeps them that way."""
    client = workload["client"]
    counts = client.get("/api/work/dashboard").json()["counts"]
    body = _history(client, bucket)
    assert body["count"] == counts[bucket]
    assert len(body["items"]) == counts[bucket]


def test_the_dates_are_the_history(workload):
    """Each row carries when the work arrived, when it was answered and when
    it was finished. Those were already on the assignment; this is the first
    place they are shown."""
    item = _history(workload["client"], "completed")["items"][0]
    assert item["assigned_at"], "when it arrived"
    assert item["responded_at"], "when it was answered"
    assert item["completed_at"], "when it was finished"
    assert item["progress"] == 100


def test_a_request_nobody_has_answered_has_no_answer_recorded(workload):
    item = _history(workload["client"], "pending")["items"][0]
    assert item["assigned_at"]
    assert item["responded_at"] is None
    assert item["completed_at"] is None


def test_finished_work_is_never_called_overdue(owner, rahul):
    """A task finished after its date was late, not overdue. Saying "overdue"
    of something already done is simply wrong, and the row already says when
    it was completed."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = _assigned(owner, c["id"], ids["Rahul"], "Late but done",
                     due_date="2020-01-01T09:00:00Z")
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 100})

    done = _history(rahul, "completed")["items"][0]
    assert done["due_date"], "the date is still shown"
    assert done["overdue"] is False

    # The same date on work still outstanding does mean overdue.
    running = _assigned(owner, c["id"], ids["Rahul"], "Late and not done",
                        due_date="2020-01-01T09:00:00Z")
    rahul.post(f"/api/work/tasks/{running['id']}/respond", json={"accept": True})
    assert _history(rahul, "active")["items"][0]["overdue"] is True


def test_completed_work_is_ordered_by_when_it_finished(owner, rahul):
    """Ordering a completed list by when the work arrived buries what was just
    finished at the bottom of it."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])

    first = _assigned(owner, c["id"], ids["Rahul"], "Finished first")
    second = _assigned(owner, c["id"], ids["Rahul"], "Finished second")
    for t in (first, second):
        rahul.post(f"/api/work/tasks/{t['id']}/respond", json={"accept": True})
    # Completed in the opposite order to the one they arrived in.
    rahul.put(f"/api/work/tasks/{second['id']}/progress", json={"progress": 100})
    rahul.put(f"/api/work/tasks/{first['id']}/progress", json={"progress": 100})

    titles = [i["title"] for i in _history(rahul, "completed")["items"]]
    assert titles == ["Finished first", "Finished second"], "most recently finished first"


def test_one_persons_history_is_their_own(workload, owner):
    """The counts are personal, so the set behind them is too. The owner
    assigned all of this and has none of it."""
    for bucket in ("active", "pending", "completed"):
        assert _history(owner, bucket)["items"] == []


def test_an_invented_bucket_is_refused(workload):
    r = workload["client"].get("/api/work/history/everything")
    assert r.status_code == 404
    assert "active" in r.json()["detail"], "and it says what the choices are"


def test_a_stranger_gets_nothing(workload):
    assert TestClient(app).get("/api/work/history/active").status_code == 401


@pytest.mark.parametrize("bucket", ["active", "pending", "completed"])
def test_the_page_opens_and_says_which_history_it_is(workload, bucket):
    page = workload["client"].get(f"/work/history/{bucket}")
    assert page.status_code == 200
    assert f'data-bucket="{bucket}"' in page.text
    assert 'id="wk-history-back"' in page.text, "and it has a way back"


def test_the_page_refuses_a_bucket_the_api_would_refuse(workload):
    """A shell that renders for any word in the path puts a heading and a
    spinner on screen before the fetch behind it 404s."""
    r = workload["client"].get("/work/history/everything", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/work"


@pytest.mark.parametrize("bucket", ["active", "pending", "completed"])
def test_the_nav_keeps_its_place_while_reading_a_history(workload, bucket):
    """These pages hang off the Work dashboard and return to it. A nav with
    nothing marked leaves the reader with no idea where they are."""
    page = workload["client"].get(f"/work/history/{bucket}").text
    marked = page.split('href="/work"')[0].rsplit("<a", 1)[1]
    assert "active" in marked, "the Work dashboard tab stays lit"


def test_the_tiles_are_links_to_it(workload):
    """They printed a number and stopped there. Anchors, not click handlers,
    so each carries a real destination."""
    page = workload["client"].get("/work").text
    for bucket in ("active", "pending", "completed"):
        assert f'href="/work/history/{bucket}"' in page
