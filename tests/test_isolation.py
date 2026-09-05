"""
One professor must never reach another's data.

A sweep rather than a handful of cases: every endpoint that takes a resource
id is asked for somebody else's, and the answer has to be a refusal AND the
resource has to be unchanged afterwards. A 404 that still performed the write
is not isolation.

The reason this is a sweep and not a list of the ones that looked risky is
that isolation fails by omission -- one handler that forgot its user filter
looks exactly like the twenty that did not.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app

REFUSED = {401, 403, 404}


def _client(email, name="Person"):
    c = TestClient(app)
    c.post("/api/auth/register", json={"name": name, "email": email, "password": "password123", "accepted_terms": True})
    return c


def _complete_work_profile(c):
    college = c.get("/api/org/colleges").json()["colleges"][0]
    dept = c.get(f"/api/org/colleges/{college['id']}/departments").json()["departments"][0]
    c.put("/api/org/profile", json={"college_id": college["id"], "department_id": dept["id"]})


@pytest.fixture
def alice():
    c = _client("iso_alice@example.com", "Alice")
    _complete_work_profile(c)
    return c


@pytest.fixture
def mallory():
    c = _client("iso_mallory@example.com", "Mallory")
    _complete_work_profile(c)
    return c


def _event(client, title="Alice Lecture"):
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    created = client.post("/api/events?force=true", json={
        "title": title, "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat(),
    }).json()
    # The endpoint answers with a list: one request can create a whole series.
    return created[0] if isinstance(created, list) else created


# --- Personal schedule -----------------------------------------------------

def test_another_professors_event_is_unreachable_by_every_route(alice, mallory):
    ev = _event(alice)
    eid = ev["id"]

    attempts = [
        ("GET", f"/api/events/{eid}", None),
        ("GET", f"/api/events/{eid}/location", None),
        ("GET", f"/api/events/{eid}/reminders", None),
        ("GET", f"/api/events/{eid}/conflict-resolution", None),
        ("POST", f"/api/events/{eid}/reminders", {"minutes_before": 30}),
        ("POST", f"/api/events/{eid}/duplicate", {}),
        ("PUT", f"/api/events/{eid}", {"title": "Stolen"}),
        ("DELETE", f"/api/events/{eid}", None),
    ]
    for method, url, body in attempts:
        r = mallory.request(method, url, json=body)
        assert r.status_code in REFUSED, f"{method} {url} returned {r.status_code}"

    # Refused is not enough: the row has to be untouched.
    still = alice.get(f"/api/events/{eid}")
    assert still.status_code == 200
    assert still.json()["title"] == "Alice Lecture"


def test_a_series_cannot_be_deleted_through_someone_elses_occurrence(alice, mallory, db_session):
    """apply_to_series widened the delete to every row sharing a group id,
    with no user filter on that second query -- so the scoping on the first
    lookup was the only thing standing between one professor's series and
    another's."""
    from app.models import Event

    ev = _event(alice, "Alice Series")
    row = db_session.query(Event).filter(Event.id == ev["id"]).first()
    row.recurrence_group_id = "shared-group-id"
    db_session.commit()

    mine = _event(mallory, "Mallory Series")
    mine_row = db_session.query(Event).filter(Event.id == mine["id"]).first()
    mine_row.recurrence_group_id = "shared-group-id"
    db_session.commit()

    mallory.delete(f"/api/events/{mine['id']}?apply_to_series=true")

    assert alice.get(f"/api/events/{ev['id']}").status_code == 200, \
        "deleting a series took an event belonging to someone else"


def test_a_series_cannot_be_edited_through_someone_elses_occurrence(alice, mallory, db_session):
    from app.models import Event

    ev = _event(alice, "Alice Untouched")
    db_session.query(Event).filter(Event.id == ev["id"]).first().recurrence_group_id = "grp"
    mine = _event(mallory, "Mallory Own")
    db_session.query(Event).filter(Event.id == mine["id"]).first().recurrence_group_id = "grp"
    db_session.commit()

    mallory.put(f"/api/events/{mine['id']}?apply_to_series=true", json={"title": "Renamed"})

    assert alice.get(f"/api/events/{ev['id']}").json()["title"] == "Alice Untouched", \
        "a series edit renamed another professor's event"


# --- Reminders, tasks, devices --------------------------------------------

def test_another_professors_reminder_cannot_be_deleted(alice, mallory):
    ev = _event(alice)
    alice.post(f"/api/events/{ev['id']}/reminders", json={"minutes_before": 30})
    rid = alice.get(f"/api/events/{ev['id']}/reminders").json()["reminders"][0]["id"]

    assert mallory.delete(f"/api/reminders/{rid}").status_code in REFUSED
    assert alice.get(f"/api/events/{ev['id']}").status_code == 200


def test_another_professors_task_is_unreachable(alice, mallory):
    task = alice.post("/api/tasks", json={"title": "Alice task"}).json()
    tid = task["id"]

    for method, url, body in [
        ("PUT", f"/api/tasks/{tid}", {"title": "Stolen"}),
        ("POST", f"/api/tasks/{tid}/complete", {}),
        ("DELETE", f"/api/tasks/{tid}", None),
    ]:
        assert mallory.request(method, url, json=body).status_code in REFUSED, f"{method} {url}"

    mine = [t for t in alice.get("/api/tasks").json() if t["id"] == tid]
    assert mine and mine[0]["title"] == "Alice task"


# --- Lists must not leak ---------------------------------------------------

def test_list_endpoints_return_only_your_own_rows(alice, mallory):
    _event(alice, "Alice Only")
    alice.post("/api/tasks", json={"title": "Alice Task Only"})

    now = datetime.now(timezone.utc)
    window = {"start": (now - timedelta(days=1)).isoformat(),
              "end": (now + timedelta(days=7)).isoformat()}

    events = mallory.get("/api/events", params=window).json()
    events = events if isinstance(events, list) else events.get("events", [])
    assert all(e["title"] != "Alice Only" for e in events)

    tasks = mallory.get("/api/tasks").json()
    assert all(t["title"] != "Alice Task Only" for t in tasks)

    assert mallory.get("/api/reminders").json() == []


# --- Work -----------------------------------------------------------------

def test_an_outsider_cannot_reach_a_community_or_its_tasks(alice, mallory):
    community = alice.post("/api/work/communities", json={"name": "Alice Team"}).json()
    cid = community["id"]

    attempts = [
        ("GET", f"/api/work/communities/{cid}", None),
        ("GET", f"/api/work/communities/{cid}/directory", None),
        ("GET", f"/api/work/communities/{cid}/deletion-preview", None),
        ("POST", f"/api/work/communities/{cid}/invite", {"email": "iso_mallory@example.com"}),
        ("POST", f"/api/work/communities/{cid}/tasks", {"title": "x", "assignee_ids": []}),
        ("DELETE", f"/api/work/communities/{cid}", {"confirm": "Alice Team", "ticket": "x"}),
    ]
    for method, url, body in attempts:
        assert mallory.request(method, url, json=body).status_code in REFUSED, f"{method} {url}"

    assert alice.get(f"/api/work/communities/{cid}").status_code == 200


def test_an_outsider_cannot_read_or_answer_a_work_task(alice, mallory, db_session):
    from app.models import CommunityMember, User

    community = alice.post("/api/work/communities", json={"name": "Task Team"}).json()
    # A second real member, so the task exists and is assigned to somebody.
    bob = _client("iso_bob@example.com", "Bob")
    _complete_work_profile(bob)
    bob_id = db_session.query(User).filter(User.email == "iso_bob@example.com").first().id
    db_session.add(CommunityMember(community_id=community["id"], user_id=bob_id, role="member"))
    db_session.commit()

    task = alice.post(f"/api/work/communities/{community['id']}/tasks",
                      json={"title": "Private work", "assignee_ids": [bob_id]}).json()
    tid = task["id"]

    for method, url, body in [
        ("GET", f"/api/work/tasks/{tid}", None),
        ("POST", f"/api/work/tasks/{tid}/respond", {"accept": True}),
        ("PUT", f"/api/work/tasks/{tid}/progress", {"progress": 100}),
        ("POST", f"/api/work/tasks/{tid}/comments", {"body": "hello"}),
        ("DELETE", f"/api/work/tasks/{tid}", None),
    ]:
        assert mallory.request(method, url, json=body).status_code in REFUSED, f"{method} {url}"

    assert alice.get(f"/api/work/tasks/{tid}").status_code == 200


def test_an_invitation_can_only_be_answered_by_its_invitee(alice, mallory):
    community = alice.post("/api/work/communities", json={"name": "Invite Team"}).json()
    bob = _client("iso_bob2@example.com", "Bob")
    _complete_work_profile(bob)
    alice.post(f"/api/work/communities/{community['id']}/invite",
               json={"email": "iso_bob2@example.com"})
    invite_id = bob.get("/api/work/invitations").json()["invitations"][0]["id"]

    assert mallory.post(f"/api/work/invitations/{invite_id}/respond",
                        json={"accept": True}).status_code in REFUSED
    # Still Bob's to answer.
    assert bob.post(f"/api/work/invitations/{invite_id}/respond",
                    json={"accept": True}).status_code == 200


# --- Notifications ---------------------------------------------------------

def test_notifications_are_private_and_cannot_be_dismissed_by_others(alice, mallory, db_session):
    from app.models import WorkNotification

    n = WorkNotification(user_id=db_session.query(
        __import__("app.models", fromlist=["User"]).User).filter_by(
        email="iso_alice@example.com").first().id,
        kind="task_assigned", title="Alice private notice")
    db_session.add(n)
    db_session.commit()

    assert all(item["title"] != "Alice private notice"
               for item in mallory.get("/api/work/notifications").json()["items"])
    assert mallory.delete(f"/api/work/notifications/{n.id}").status_code in REFUSED
    assert any(item["title"] == "Alice private notice"
               for item in alice.get("/api/work/notifications").json()["items"])


# --- Exports and analytics -------------------------------------------------

def test_exports_and_analytics_only_ever_describe_the_caller(alice, mallory):
    _event(alice, "Alice Export Only")

    for path in ("/api/export/csv", "/api/export/ics"):
        r = mallory.get(path)
        if r.status_code == 200:
            assert "Alice Export Only" not in r.text, f"{path} leaked another professor's event"

    stats = mallory.get("/api/analytics")
    if stats.status_code == 200:
        blob = stats.text
        assert "Alice Export Only" not in blob


def test_every_protected_route_refuses_an_anonymous_caller(client):
    for method, path in [
        ("GET", "/api/events"), ("GET", "/api/tasks"), ("GET", "/api/reminders"),
        ("GET", "/api/work/dashboard"), ("GET", "/api/work/communities"),
        ("GET", "/api/work/notifications"), ("GET", "/api/org/profile"),
        ("GET", "/api/analytics"), ("PUT", "/api/auth/me"),
    ]:
        assert client.request(method, path, json={}).status_code == 401, f"{method} {path}"
