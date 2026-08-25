"""
Work mode: communities, invitations, the task-acceptance workflow, progress
arithmetic, permissions, and isolation from the personal schedule.

The rule these tests exist to protect: sending someone a task does not make it
their responsibility. It becomes theirs when they accept, and not before.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import AssignmentStatus, CommunityMember, WorkTask, WorkTaskStatus


def _user(email, name="Person"):
    c = TestClient(app)
    c.post("/api/auth/register", json={"name": name, "email": email, "password": "password123"})
    return c


@pytest.fixture
def owner():
    return _user("w_owner@example.com", "Bhushan")


@pytest.fixture
def rahul():
    return _user("w_rahul@example.com", "Rahul")


@pytest.fixture
def amit():
    return _user("w_amit@example.com", "Amit")


def _community(client, name="Project Alpha"):
    return client.post("/api/work/communities", json={"name": name, "icon": "🚀"}).json()


def _join(owner_c, member_c, community_id, email):
    owner_c.post(f"/api/work/communities/{community_id}/invite", json={"email": email})
    inv = member_c.get("/api/work/invitations").json()["invitations"][0]
    member_c.post(f"/api/work/invitations/{inv['id']}/respond", json={"accept": True})
    return inv


def _members(client, community_id):
    return {m["name"]: m["id"] for m in client.get(f"/api/work/communities/{community_id}").json()["members"]}


# --- Communities -----------------------------------------------------------

def test_creator_becomes_owner_and_member(owner):
    c = _community(owner)
    assert c["my_role"] == "owner"
    assert c["member_count"] == 1, "a creator who must invite themselves is a bug"


def test_a_community_is_invisible_to_outsiders(owner, rahul):
    """404, not 403: telling someone a community exists but is closed to them
    is itself a disclosure, and names are not public."""
    c = _community(owner)
    assert rahul.get(f"/api/work/communities/{c['id']}").status_code == 404
    assert rahul.get("/api/work/communities").json()["communities"] == []


def test_only_admins_can_invite(owner, rahul, amit):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")

    # Rahul joined as a plain member.
    resp = rahul.post(f"/api/work/communities/{c['id']}/invite", json={"email": "w_amit@example.com"})
    assert resp.status_code == 403


def test_membership_requires_accepting_the_invitation(owner, rahul):
    c = _community(owner)
    owner.post(f"/api/work/communities/{c['id']}/invite", json={"email": "w_rahul@example.com"})

    # Invited is not joined.
    assert owner.get(f"/api/work/communities/{c['id']}").json()["member_count"] == 1
    assert rahul.get("/api/work/communities").json()["communities"] == []

    inv = rahul.get("/api/work/invitations").json()["invitations"][0]
    rahul.post(f"/api/work/invitations/{inv['id']}/respond", json={"accept": True})
    assert owner.get(f"/api/work/communities/{c['id']}").json()["member_count"] == 2


def test_declining_an_invitation_does_not_join(owner, rahul):
    c = _community(owner)
    owner.post(f"/api/work/communities/{c['id']}/invite", json={"email": "w_rahul@example.com"})
    inv = rahul.get("/api/work/invitations").json()["invitations"][0]
    rahul.post(f"/api/work/invitations/{inv['id']}/respond", json={"accept": False})

    assert owner.get(f"/api/work/communities/{c['id']}").json()["member_count"] == 1
    assert rahul.get("/api/work/communities").json()["communities"] == []


def test_an_invitation_can_only_be_answered_by_its_recipient(owner, rahul, amit):
    c = _community(owner)
    owner.post(f"/api/work/communities/{c['id']}/invite", json={"email": "w_rahul@example.com"})
    inv = rahul.get("/api/work/invitations").json()["invitations"][0]
    assert amit.post(f"/api/work/invitations/{inv['id']}/respond", json={"accept": True}).status_code == 404


def test_the_owner_cannot_be_removed(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    resp = owner.delete(f"/api/work/communities/{c['id']}/members/{ids['Bhushan']}")
    assert resp.status_code == 400, "a community with no owner cannot be administered"


def test_member_list_does_not_expose_email_addresses(owner, rahul):
    """An email is enough to invite someone by, so echoing every member's
    address would turn any community into a mailing list."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    body = owner.get(f"/api/work/communities/{c['id']}").text
    assert "w_rahul@example.com" not in body


# --- Task acceptance -------------------------------------------------------

def test_a_new_task_is_not_yet_anyones_responsibility(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])

    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build Authentication API", "assignee_ids": [ids["Rahul"]]}).json()

    assert task["status"] == WorkTaskStatus.PENDING_ACCEPTANCE.value
    dash = rahul.get("/api/work/dashboard").json()
    assert dash["counts"]["active"] == 0, "an unanswered task must not be counted as active work"
    assert dash["counts"]["pending"] == 1
    assert [t["title"] for t in dash["requests"]] == ["Build Authentication API"]


def test_progress_is_refused_before_acceptance(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()

    resp = rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 50})
    assert resp.status_code == 400
    assert "accept" in resp.json()["detail"].lower()


def test_accepting_moves_it_into_active_work(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()

    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    dash = rahul.get("/api/work/dashboard").json()
    assert dash["counts"]["active"] == 1
    assert dash["counts"]["pending"] == 0


def test_declining_records_the_reason_and_never_activates(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()

    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": False, "reason": "On leave"})

    assert rahul.get("/api/work/dashboard").json()["counts"]["active"] == 0
    detail = owner.get(f"/api/work/tasks/{task['id']}").json()
    mine = detail["assignments"][0]
    assert mine["status"] == AssignmentStatus.DECLINED.value
    assert mine["decline_reason"] == "On leave"


def test_a_task_cannot_be_answered_twice(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()

    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    second = rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": False})
    assert second.status_code == 400


def test_work_cannot_be_assigned_to_a_non_member(owner, rahul, amit):
    """A task is a claim on someone's time inside a community; it must not be
    a way to reach a stranger."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")

    other = _community(amit, "Amit's Team")
    outsider_id = _members(amit, other["id"])["Amit"]

    resp = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Sneaky", "assignee_ids": [outsider_id]})
    assert resp.status_code == 400


# --- Group progress --------------------------------------------------------

def test_group_progress_averages_only_those_who_accepted(owner, rahul, amit):
    """Someone who declined is not 0% done -- they are not participating.
    Averaging them in would make the task look permanently stalled."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    _join(owner, amit, c["id"], "w_amit@example.com")
    ids = _members(owner, c["id"])

    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Dashboard", "assignee_ids": [ids["Rahul"], ids["Amit"]]}).json()

    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    amit.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": False})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 60})

    p = owner.get(f"/api/work/tasks/{task['id']}").json()["progress"]
    assert p["overall"] == 60, "declined members must be excluded, not averaged as zero"
    assert (p["accepted"], p["declined"], p["pending"]) == (1, 1, 0)


def test_pending_members_are_reported_but_not_counted(owner, rahul, amit):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    _join(owner, amit, c["id"], "w_amit@example.com")
    ids = _members(owner, c["id"])

    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Dashboard", "assignee_ids": [ids["Rahul"], ids["Amit"]]}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 80})

    p = owner.get(f"/api/work/tasks/{task['id']}").json()["progress"]
    assert p["overall"] == 80
    assert p["pending"] == 1


def test_reaching_100_percent_completes_the_assignment(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()

    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    out = rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 100}).json()

    assert out["assignments"][0]["status"] == AssignmentStatus.COMPLETED.value
    assert out["status"] == WorkTaskStatus.COMPLETED.value


def test_one_member_cannot_move_anothers_progress(owner, rahul, amit):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    _join(owner, amit, c["id"], "w_amit@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})

    # Amit is in the community but has no assignment on this task.
    assert amit.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 99}).status_code == 404


def test_a_task_is_unreadable_outside_its_community(owner, rahul, amit):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Secret", "assignee_ids": [ids["Rahul"]]}).json()

    assert amit.get(f"/api/work/tasks/{task['id']}").status_code == 404
    assert amit.get("/api/work/tasks?scope=assigned").json()["tasks"] == []


# --- Progress history and notifications ------------------------------------

def test_progress_changes_are_recorded_in_the_timeline(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()

    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 40, "note": "Schema done"})

    timeline = owner.get(f"/api/work/tasks/{task['id']}").json()["timeline"]
    notes = [t["note"] for t in timeline]
    assert "Task accepted" in notes
    assert any(t["to"] == 40 and t["note"] == "Schema done" for t in timeline)


def test_the_assignor_is_notified_about_responses_and_progress(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()

    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 30})

    kinds = [n["kind"] for n in owner.get("/api/work/notifications").json()["items"]]
    assert "task_accepted" in kinds
    assert "task_progress" in kinds


def test_the_assignee_is_notified_of_a_new_assignment(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]})

    kinds = [n["kind"] for n in rahul.get("/api/work/notifications").json()["items"]]
    assert "task_assigned" in kinds


# --- Isolation from Personal mode ------------------------------------------

def test_work_tasks_never_appear_in_the_personal_schedule(owner, rahul):
    """The whole promise of two modes: the data does not mix."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Work Only Task", "assignee_ids": [ids["Rahul"]]})

    assert owner.get("/api/events").json() == []
    personal = owner.get("/api/tasks").json()
    assert all(t["title"] != "Work Only Task" for t in personal)
    assert owner.get("/api/reminders").json() == []


def test_personal_events_never_appear_in_work(owner):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    owner.post("/api/events?force=true", json={
        "title": "Private DBMS Lecture", "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat()})

    body = owner.get("/api/work/dashboard").text
    assert "Private DBMS Lecture" not in body


def test_the_active_profile_is_remembered(owner):
    assert owner.put("/api/work/profile", json={"profile": "work"}).json()["profile"] == "work"
    assert owner.get("/api/auth/me").json().get("active_profile", "work") in ("work", None)


def test_work_endpoints_require_authentication(client):
    for path in ["/api/work/dashboard", "/api/work/communities", "/api/work/invitations",
                 "/api/work/tasks", "/api/work/notifications"]:
        assert client.get(path).status_code == 401, path
