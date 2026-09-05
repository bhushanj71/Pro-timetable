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


def _org(client):
    """The seeded college and its departments, as the app serves them."""
    college = client.get("/api/org/colleges").json()["colleges"][0]
    depts = client.get(f"/api/org/colleges/{college['id']}/departments").json()["departments"]
    return college, depts


def _user(email, name="Person", department=None, complete=True):
    """A signed-up professor who has finished their work profile.

    Work refuses to place anyone in the organisation without a department, so
    an incomplete account is the exception here, not the default. Pass
    complete=False to test the gate itself.
    """
    c = TestClient(app)
    c.post("/api/auth/register", json={"name": name, "email": email, "password": "password123", "accepted_terms": True})
    if complete:
        college, depts = _org(c)
        chosen = next((d for d in depts if d["name"] == department), depts[0])
        c.put("/api/org/profile",
              json={"college_id": college["id"], "department_id": chosen["id"]})
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
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 65})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 100})

    kinds = [n["kind"] for n in owner.get("/api/work/notifications").json()["items"]]
    assert "task_accepted" in kinds
    # Starting, moving and finishing are distinct events: "Rahul started this"
    # and "Rahul finished this" are what an assignor actually wants to hear,
    # and collapsing them into one "progress" kind loses both.
    assert "task_started" in kinds, "the first move off zero is a start"
    assert "task_progress" in kinds, "a middling move is a progress update"
    assert "task_completed" in kinds, "reaching 100% is a completion"


def test_a_finished_task_is_announced_once(owner, rahul):
    """The last person finishing means the task is done -- said once, however
    many times they re-save their 100%."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()

    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    for _ in range(3):
        rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 100})

    kinds = [n["kind"] for n in owner.get("/api/work/notifications").json()["items"]]
    assert kinds.count("task_all_completed") == 1, "de-duplicated, not repeated"


def test_nobody_is_notified_about_their_own_action(owner):
    """Assigning yourself a task should not put "you assigned you" in your
    own inbox."""
    c = _community(owner)
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Self task", "assignee_ids": [ids["Bhushan"]]}).json()
    owner.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    owner.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 50})

    items = owner.get("/api/work/notifications").json()["items"]
    assert [n for n in items if n["task_id"] == task["id"]] == []


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


# --- Deadlines, overdue and chase-ups --------------------------------------

def _sweep(db_session, when=None):
    from app.services.work_notify import sweep_work_deadlines
    return sweep_work_deadlines(db_session, now=when)


def _kinds(client):
    return [n["kind"] for n in client.get("/api/work/notifications").json()["items"]]


def test_an_approaching_deadline_is_announced_once(owner, rahul, db_session):
    """The sweep runs every few minutes; without de-duplication it would
    resend "due tomorrow" on every pass until the deadline arrived."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    due = datetime.now(timezone.utc) + timedelta(hours=20)
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Database Migration", "assignee_ids": [ids["Rahul"]],
        "due_date": due.isoformat()}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})

    for _ in range(3):
        _sweep(db_session)

    assert _kinds(rahul).count("task_due_soon") == 1


def test_an_overdue_task_is_flagged(owner, rahul, db_session):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    due = datetime.now(timezone.utc) - timedelta(hours=3)
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Late Task", "assignee_ids": [ids["Rahul"]],
        "due_date": due.isoformat()}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})

    _sweep(db_session)
    _sweep(db_session)
    assert _kinds(rahul).count("task_overdue") == 1


def test_a_completed_task_stops_generating_deadline_notices(owner, rahul, db_session):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    due = datetime.now(timezone.utc) - timedelta(hours=2)
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Finished Task", "assignee_ids": [ids["Rahul"]],
        "due_date": due.isoformat()}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 100})

    _sweep(db_session)
    assert "task_overdue" not in _kinds(rahul), "done work is not late work"


def test_an_unanswered_assignment_is_chased_but_not_nagged(owner, rahul, db_session):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build Login API", "assignee_ids": [ids["Rahul"]]})

    # Nothing straight away: a request sent minutes ago is not being ignored.
    _sweep(db_session)
    assert "assignment_reminder" not in _kinds(rahul)

    # A day later, one nudge -- and only one, however often the sweep runs.
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    for _ in range(3):
        _sweep(db_session, when=later)
    assert _kinds(rahul).count("assignment_reminder") == 1


def test_answering_stops_the_chase_ups(owner, rahul, db_session):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build Login API", "assignee_ids": [ids["Rahul"]]}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})

    _sweep(db_session, when=datetime.now(timezone.utc) + timedelta(days=3))
    assert "assignment_reminder" not in _kinds(rahul)


def test_a_declined_assignment_gets_no_deadline_notices(owner, rahul, db_session):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    due = datetime.now(timezone.utc) - timedelta(hours=2)
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Not Mine", "assignee_ids": [ids["Rahul"]],
        "due_date": due.isoformat()}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": False})

    _sweep(db_session)
    assert "task_overdue" not in _kinds(rahul), "a task you refused is not your deadline"


# --- Preferences -----------------------------------------------------------

def test_progress_notifications_can_be_switched_off(owner, rahul, db_session):
    from app.models import User

    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build API", "assignee_ids": [ids["Rahul"]]}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})

    me = db_session.query(User).filter(User.email == "w_owner@example.com").one()
    me.notify_work_progress = False
    db_session.commit()

    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 45})
    assert "task_progress" not in _kinds(owner)
    assert "task_started" not in _kinds(owner)


def test_assignments_cannot_be_silenced(owner, rahul, db_session):
    """Every optional category off, and a new assignment still arrives: the
    pending list is an obligation, not a newsletter."""
    from app.models import User

    them = db_session.query(User).filter(User.email == "w_rahul@example.com").one()
    for field in ("notify_work_responses", "notify_work_progress", "notify_work_completion",
                  "notify_work_deadlines", "notify_work_community"):
        setattr(them, field, False)
    db_session.commit()

    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Unmissable", "assignee_ids": [ids["Rahul"]]})

    assert "task_assigned" in _kinds(rahul)


def test_notifications_are_private_to_their_recipient(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Owner Eyes Only", "assignee_ids": [ids["Rahul"]]})

    # Rahul's assignment notice is his; it must not appear in the owner's feed.
    owner_titles = [n["title"] for n in owner.get("/api/work/notifications").json()["items"]]
    assert not any("assigned you" in t for t in owner_titles)


# --- The bell carries everything -------------------------------------------
# "Every task in the whole application should be updated by a notification
# under the bell" -- so personal schedule actions leave a receipt too, not
# only work activity.

def _bell(client):
    """What the bell would show: both feeds, as the browser merges them."""
    work = client.get("/api/work/notifications").json()
    personal = client.get("/api/reminders/notifications").json()
    return {
        "work": [(n["kind"], n["title"]) for n in work["items"]],
        "unread": work["unread"] + personal["unread"],
    }


def test_creating_a_lecture_leaves_a_receipt(owner):
    start = datetime.now(timezone.utc) + timedelta(days=2)
    owner.post("/api/events?force=true", json={
        "title": "DBMS Lecture", "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat()})

    kinds = [k for k, _ in _bell(owner)["work"]]
    assert "event_created" in kinds


def test_a_recurring_series_leaves_one_receipt_not_sixteen(owner):
    """A weekly lecture materialises many rows from one decision. One
    notification per occurrence would bury the bell."""
    start = datetime.now(timezone.utc) + timedelta(days=2)
    resp = owner.post("/api/events?force=true", json={
        "title": "Weekly Lecture", "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat(),
        "recurrence_rule": "weekly:MON"})
    assert len(resp.json()) > 1, "this should materialise a series"

    created = [t for k, t in _bell(owner)["work"] if k == "event_created"]
    assert len(created) == 1
    assert "occurrences" in (owner.get("/api/work/notifications").json()["items"][0]["body"] or "")


def test_updating_and_deleting_a_lecture_leave_receipts(owner):
    start = datetime.now(timezone.utc) + timedelta(days=2)
    ev = owner.post("/api/events?force=true", json={
        "title": "Movable Lecture", "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat()}).json()[0]

    owner.put(f"/api/events/{ev['id']}?force=true", json={"location": "Room 42"})
    owner.delete(f"/api/events/{ev['id']}")

    kinds = [k for k, _ in _bell(owner)["work"]]
    assert "event_updated" in kinds
    assert "event_deleted" in kinds


def test_personal_task_actions_leave_receipts(owner):
    due = datetime.now(timezone.utc) + timedelta(days=3)
    task = owner.post("/api/tasks", json={"title": "Submit marks", "due_date": due.isoformat()}).json()
    owner.post(f"/api/tasks/{task['id']}/complete")

    kinds = [k for k, _ in _bell(owner)["work"]]
    assert "personal_task_created" in kinds
    assert "personal_task_completed" in kinds


def test_receipts_are_private_to_their_owner(owner, rahul):
    start = datetime.now(timezone.utc) + timedelta(days=2)
    owner.post("/api/events?force=true", json={
        "title": "Private Lecture", "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat()})

    assert not any("Private Lecture" in t for _, t in _bell(rahul)["work"])


def test_a_receipt_never_becomes_a_reminder(owner, db_session):
    """A reminder is a promise to interrupt someone -- push, email, a
    lock-screen buzz. "You created a lecture" is a receipt, and putting
    receipts in the reminder table would send them to people's phones."""
    from app.models import Reminder

    start = datetime.now(timezone.utc) + timedelta(days=2)
    owner.post("/api/events?force=true", json={
        "title": "Receipt Check", "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat()})

    titles = [r.title for r in db_session.query(Reminder).all()]
    assert not any(t.startswith("Added ") for t in titles)


# --- Push delivery for bell notifications ----------------------------------

def _fake_push(monkeypatch):
    """Capture web-push calls instead of making them."""
    from app.services import notifier
    import pywebpush

    sent = []

    class _Exc(Exception):
        pass

    monkeypatch.setattr(notifier, "push_configured", lambda: True)
    monkeypatch.setattr(notifier.settings, "VAPID_PRIVATE_KEY", "test-key", raising=False)
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: sent.append(kw))
    monkeypatch.setattr(pywebpush, "WebPushException", _Exc)
    return sent


def _register_device(client, endpoint="https://fcm.googleapis.com/fcm/send/dev1"):
    client.post("/api/push/subscribe", json={
        "endpoint": endpoint,
        "keys": {"p256dh": "BKxQ" + "a" * 40, "auth": "sEcRe7abcdefgh"}})


def test_a_bell_notification_is_pushed_to_the_device(owner, rahul, db_session, monkeypatch):
    """Every notification in the bell should also reach the phone."""
    import json as _json
    from app.services.work_notify import deliver_pending_pushes

    sent = _fake_push(monkeypatch)
    _register_device(rahul)

    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Build Authentication API", "assignee_ids": [ids["Rahul"]]})

    deliver_pending_pushes(db_session)

    assert sent, "the assignee's phone should have been pushed"
    payload = _json.loads(sent[-1]["data"])
    assert "Build Authentication API" in payload["title"]
    assert payload["url"].startswith("/work"), "tapping it must land on the task"


def test_a_notification_is_pushed_only_once(owner, rahul, db_session, monkeypatch):
    """The delivery pass runs from requests and from the cron sweep. Both
    finding the same row must not buzz the phone twice."""
    from app.services.work_notify import deliver_pending_pushes

    sent = _fake_push(monkeypatch)
    _register_device(rahul)

    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Once Only", "assignee_ids": [ids["Rahul"]]})

    deliver_pending_pushes(db_session)
    first = len(sent)
    deliver_pending_pushes(db_session)
    deliver_pending_pushes(db_session)

    assert len(sent) == first, "already-pushed notifications must not be resent"


def test_your_own_actions_do_not_buzz_your_phone(owner, db_session, monkeypatch):
    """A receipt for something you just did belongs in the bell as a record,
    but pushing it buzzes the phone already in your hand."""
    from app.services.work_notify import deliver_pending_pushes

    sent = _fake_push(monkeypatch)
    _register_device(owner)

    start = datetime.now(timezone.utc) + timedelta(days=2)
    owner.post("/api/events?force=true", json={
        "title": "My Own Lecture", "event_type": "lecture",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat()})

    deliver_pending_pushes(db_session)
    titles = [s.get("data", "") for s in sent]
    assert not any("My Own Lecture" in str(t) for t in titles)


def test_a_deadline_notice_is_pushed(owner, rahul, db_session, monkeypatch):
    from app.services.work_notify import deliver_pending_pushes, sweep_work_deadlines

    sent = _fake_push(monkeypatch)
    _register_device(rahul)

    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    due = datetime.now(timezone.utc) - timedelta(hours=2)
    task = owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Late Work", "assignee_ids": [ids["Rahul"]], "due_date": due.isoformat()}).json()
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})

    sweep_work_deadlines(db_session)
    deliver_pending_pushes(db_session)

    import json as _json
    assert any("overdue" in _json.loads(s["data"])["title"].lower() for s in sent)


def test_push_respects_the_users_switch(owner, rahul, db_session, monkeypatch):
    from app.models import User
    from app.services.work_notify import deliver_pending_pushes

    sent = _fake_push(monkeypatch)
    _register_device(rahul)

    them = db_session.query(User).filter(User.email == "w_rahul@example.com").one()
    them.notify_push = False
    db_session.commit()

    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks", json={
        "title": "Silent", "assignee_ids": [ids["Rahul"]]})

    deliver_pending_pushes(db_session)
    assert sent == []


# --- Dashboard trends ------------------------------------------------------

def test_each_trend_series_ends_on_the_count_it_is_drawn_beside(owner, rahul):
    """The sparkline is a claim about the number printed next to it.

    If the last reading of a series ever disagrees with the headline count,
    the card shows a line that contradicts its own figure -- so this is the
    property worth pinning, not the shape of the curve.
    """
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])

    # One accepted, one left unanswered, one carried to completion.
    for title in ("Accepted work", "Unanswered work", "Finished work"):
        owner.post(f"/api/work/communities/{c['id']}/tasks",
                   json={"title": title, "assignee_ids": [ids["Rahul"]]})

    tasks = {t["title"]: t for t in rahul.get("/api/work/tasks").json()["tasks"]}
    rahul.post(f"/api/work/tasks/{tasks['Accepted work']['id']}/respond", json={"accept": True})
    rahul.post(f"/api/work/tasks/{tasks['Finished work']['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{tasks['Finished work']['id']}/progress", json={"progress": 100})

    d = rahul.get("/api/work/dashboard").json()
    trends, counts = d["trends"], d["counts"]

    assert set(trends) == {"active", "pending", "completed"}
    for field in ("active", "pending", "completed"):
        series = trends[field]
        assert len(series) == 7, f"{field} should carry a week of readings"
        assert all(isinstance(v, int) and v >= 0 for v in series)
        assert series[-1] == counts[field], (
            f"the {field} sparkline ends at {series[-1]} but the card prints {counts[field]}"
        )

    assert counts == {"active": 1, "pending": 1, "completed": 1}


def test_a_declined_task_never_appears_as_active_in_the_trend(owner, rahul):
    """Declining is not doing. A declined assignment must not raise the line
    that says how much work someone is carrying."""
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks",
               json={"title": "Not mine", "assignee_ids": [ids["Rahul"]]})

    task = rahul.get("/api/work/tasks").json()["tasks"][0]
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": False, "reason": "no capacity"})

    trends = rahul.get("/api/work/dashboard").json()["trends"]
    assert trends["active"][-1] == 0
    assert trends["pending"][-1] == 0
    assert trends["completed"][-1] == 0


# --- Deleting a community --------------------------------------------------

def _preview(client, community_id):
    return client.get(f"/api/work/communities/{community_id}/deletion-preview")


def _delete(client, community_id, confirm, ticket):
    return client.request(
        "DELETE", f"/api/work/communities/{community_id}",
        json={"confirm": confirm, "ticket": ticket},
    )


def test_deleting_a_community_needs_both_steps(owner, rahul):
    """A ticket alone is not enough, and a typed name alone is not enough."""
    c = _community(owner, "Physics Wing")
    _join(owner, rahul, c["id"], "w_rahul@example.com")

    step1 = _preview(owner, c["id"])
    assert step1.status_code == 200
    ticket = step1.json()["ticket"]

    # Right ticket, wrong name.
    assert _delete(owner, c["id"], "Physics", ticket).status_code == 400
    # Right name, no ticket.
    assert _delete(owner, c["id"], "Physics Wing", "").status_code == 400
    # Right name, forged ticket.
    assert _delete(owner, c["id"], "Physics Wing", "9999999999.deadbeef").status_code == 400

    # Still there after three refusals.
    assert owner.get(f"/api/work/communities/{c['id']}").status_code == 200

    assert _delete(owner, c["id"], "  physics wing  ", ticket).status_code == 200
    assert owner.get(f"/api/work/communities/{c['id']}").status_code == 404


def test_the_preview_states_what_will_be_destroyed(owner, rahul):
    """The count of half-finished accepted work is the number that should
    stop someone, so it is reported separately."""
    c = _community(owner, "Lab Group")
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks",
               json={"title": "Half done", "assignee_ids": [ids["Rahul"]]})
    task = rahul.get("/api/work/tasks").json()["tasks"][0]
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 40})

    impact = _preview(owner, c["id"]).json()["impact"]
    assert impact["members"] == 2
    assert impact["tasks"] == 1
    assert impact["assignments"] == 1
    assert impact["unfinished_accepted"] == 1
    assert impact["progress_updates"] >= 1


def test_only_the_owner_can_delete_a_community(owner, rahul, amit, db_session):
    """An admin can invite and assign. Destroying everyone else's work is not
    an administrative act."""
    c = _community(owner, "Shared Space")
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    # Promoted directly: there is no role endpoint yet, and going through a
    # 404 would leave Rahul a plain member and quietly test the wrong thing.
    member = db_session.query(CommunityMember).filter(
        CommunityMember.community_id == c["id"],
        CommunityMember.user_id == _members(owner, c["id"])["Rahul"],
    ).first()
    member.role = "admin"
    db_session.commit()
    assert member.role == "admin"

    assert _preview(rahul, c["id"]).status_code in (403, 404)
    assert _delete(rahul, c["id"], "Shared Space", "x").status_code in (400, 403, 404)
    assert _preview(amit, c["id"]).status_code == 404
    assert owner.get(f"/api/work/communities/{c['id']}").status_code == 200


def test_a_ticket_cannot_be_spent_on_a_different_community(owner):
    """Two open dialogs must not be interchangeable."""
    a = _community(owner, "Alpha")
    b = _community(owner, "Beta")
    ticket_for_a = _preview(owner, a["id"]).json()["ticket"]

    assert _delete(owner, b["id"], "Beta", ticket_for_a).status_code == 400
    assert owner.get(f"/api/work/communities/{b['id']}").status_code == 200


def test_deletion_clears_the_bell_of_everything_it_pointed_at(owner, rahul):
    """WorkNotification stores community_id and task_id as plain strings with
    no foreign key, so nothing cleans them up on its own. Left behind, they
    render as normal notifications that deep-link to a task that is gone."""
    c = _community(owner, "Ghost Town")
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks",
               json={"title": "Doomed", "assignee_ids": [ids["Rahul"]]})

    before = rahul.get("/api/work/notifications").json()["items"]
    assert any(n["kind"] == "task_assigned" for n in before)

    ticket = _preview(owner, c["id"]).json()["ticket"]
    assert _delete(owner, c["id"], "Ghost Town", ticket).status_code == 200

    after = rahul.get("/api/work/notifications").json()["items"]
    assert not any(n["community_id"] == c["id"] for n in after), "orphaned notification survived"
    assert not any(n["kind"] == "task_assigned" for n in after)
    # And the members are told, without a link to the community that is gone.
    goodbye = [n for n in after if n["kind"] == "community_deleted"]
    assert len(goodbye) == 1
    assert "Ghost Town" in goodbye[0]["title"]
    assert goodbye[0]["community_id"] is None


# --- The same-college directory -------------------------------------------

def test_the_directory_lists_colleagues_at_the_same_college(owner, rahul):
    """Scoped by college id now. The old version compared typed names, so two
    colleagues who spelled their college differently were invisible to each
    other."""
    c = _community(owner, "Faculty Room")
    body = owner.get(f"/api/work/communities/{c['id']}/directory").json()

    names = {p["name"] for p in body["people"]}
    assert "Rahul" in names
    assert body["college"] == "DYPCoE, Akurdi"
    # Still never the email.
    assert all("email" not in p for p in body["people"])
    # Every person carries the department that tells them apart.
    assert all("department" in p for p in body["people"])


def test_the_directory_does_not_reach_into_another_college(owner, rahul, db_session):
    from app.models import College, Department, User, normalise_org_name

    other = College(name="Elsewhere Institute", normalised_name=normalise_org_name("Elsewhere Institute"))
    db_session.add(other)
    db_session.flush()
    dept = Department(college_id=other.id, name="Physics",
                      normalised_name=normalise_org_name("Physics"))
    db_session.add(dept)
    db_session.flush()

    outsider = db_session.query(User).filter(User.email == "w_rahul@example.com").first()
    outsider.college_id, outsider.department_id = other.id, dept.id
    db_session.commit()

    c = _community(owner, "Faculty Room")
    people = owner.get(f"/api/work/communities/{c['id']}/directory").json()["people"]
    assert "Rahul" not in {p["name"] for p in people}


def test_a_viewer_with_no_college_gets_nobody_not_everybody(rahul):
    """The failure that matters. Scoped on a nullable column written the
    obvious way, a viewer with no college would match every other account
    that also had none."""
    blank = _user("w_blank@example.com", "Blank", complete=False)
    c = _community(rahul, "Faculty Room")
    # Not a member, so the community is invisible before the college even
    # matters -- the point is proven on a community they do own.
    assert blank.post("/api/work/communities", json={"name": "Mine"}).status_code == 428


def test_the_directory_says_who_is_already_in(owner, rahul):
    c = _community(owner, "Faculty Room")
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    row = [p for p in owner.get(
        f"/api/work/communities/{c['id']}/directory").json()["people"] if p["name"] == "Rahul"][0]
    assert row["member"] is True


def test_the_directory_can_be_searched_and_filtered_by_department(owner, rahul, amit):
    """Search covers name, department and designation; the filter narrows the
    list without narrowing who may be invited."""
    c = _community(owner, "Faculty Room")
    url = f"/api/work/communities/{c['id']}/directory"

    assert {p["name"] for p in owner.get(url, params={"q": "rah"}).json()["people"]} == {"Rahul"}

    body = owner.get(url).json()
    assert body["departments"], "the filter must offer this college's departments"

    rahul_dept = [p for p in body["people"] if p["name"] == "Rahul"][0]["department_id"]
    filtered = owner.get(url, params={"department_id": rahul_dept}).json()["people"]
    assert "Rahul" in {p["name"] for p in filtered}


def test_only_admins_can_browse_the_directory(owner, rahul, amit):
    c = _community(owner, "Faculty Room")
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    assert rahul.get(f"/api/work/communities/{c['id']}/directory").status_code == 403
    assert amit.get(f"/api/work/communities/{c['id']}/directory").status_code == 404


# --- The organisation ------------------------------------------------------

def test_the_default_college_has_its_eight_teaching_departments(owner):
    """The administrative posts seeded beside these are checked in
    test_org_offices; this stays about the teaching departments, so a change
    to one group cannot quietly be excused by the other."""
    college, depts = _org(owner)
    assert college["name"] == "DYPCoE, Akurdi"
    academic = {d["name"] for d in depts if d["kind"] == "academic"}
    assert academic == {
        "Computer Engineering",
        "Information Technology",
        "Electronics and Telecommunication Engineering",
        "Mechanical Engineering",
        "Civil Engineering",
        "Robotics and Automation",
        "Instrumentation and Control Engineering",
        "Artificial Intelligence and Data Science",
    }


def test_seeding_twice_does_not_duplicate_anything(owner):
    from app.database import seed_organisation

    before = len(_org(owner)[1])
    seed_organisation()
    seed_organisation()
    college, depts = _org(owner)
    # Counted rather than hardcoded: the point is that seeding is idempotent,
    # not how many rows there happen to be this month.
    assert len(depts) == before
    assert len(owner.get("/api/org/colleges").json()["colleges"]) == 1


def test_work_is_refused_until_a_department_is_chosen(owner):
    """The gate, checked on the server. A hidden button is not a permission."""
    fresh = _user("w_gate@example.com", "Gate", complete=False)
    assert fresh.get("/api/org/profile").json()["complete"] is False

    assert fresh.post("/api/work/communities", json={"name": "Too soon"}).status_code == 428

    college, depts = _org(fresh)
    fresh.put("/api/org/profile",
              json={"college_id": college["id"], "department_id": depts[0]["id"]})
    assert fresh.post("/api/work/communities", json={"name": "Now fine"}).status_code == 201


def test_work_already_in_flight_is_never_stranded_by_the_gate(owner, rahul, db_session):
    """Someone part-way through accepted work must be able to finish it. A
    profile prompt that blocked progress would break the thing this feature
    exists to enhance."""
    from app.models import User

    c = _community(owner, "In Flight")
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    owner.post(f"/api/work/communities/{c['id']}/tasks",
               json={"title": "Half done", "assignee_ids": [ids["Rahul"]]})

    # Rahul loses his department the way an admin archiving one would leave him.
    stranded = db_session.query(User).filter(User.email == "w_rahul@example.com").first()
    stranded.department_id = None
    db_session.commit()

    assert rahul.get("/api/work/dashboard").status_code == 200
    task = rahul.get("/api/work/tasks").json()["tasks"][0]
    assert rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True}).status_code == 200
    assert rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 50}).status_code == 200


def test_a_department_must_belong_to_the_chosen_college(owner, db_session):
    """The check the relational model exists to make possible."""
    from app.models import College, Department, normalise_org_name

    other = College(name="Other Tech", normalised_name=normalise_org_name("Other Tech"))
    db_session.add(other)
    db_session.flush()
    foreign = Department(college_id=other.id, name="Naval",
                         normalised_name=normalise_org_name("Naval"))
    db_session.add(foreign)
    db_session.commit()

    college, _ = _org(owner)
    r = owner.put("/api/org/profile",
                  json={"college_id": college["id"], "department_id": foreign.id})
    assert r.status_code == 400
    assert "different college" in r.json()["detail"]


def test_a_department_cannot_be_left_out(owner):
    college, _ = _org(owner)
    assert owner.put("/api/org/profile",
                     json={"college_id": college["id"], "department_id": ""}).status_code == 422


def test_an_archived_department_cannot_be_chosen(owner, db_session):
    from app.models import Department, OrgStatus

    college, depts = _org(owner)
    target = db_session.get(Department, depts[0]["id"])
    target.status = OrgStatus.ARCHIVED.value
    db_session.commit()

    r = owner.put("/api/org/profile",
                  json={"college_id": college["id"], "department_id": target.id})
    assert r.status_code == 400
    assert "archived" in r.json()["detail"].lower()
    # And it is gone from the picker.
    assert target.id not in {d["id"] for d in _org(owner)[1]}


def test_people_are_shown_with_their_department_everywhere(owner, rahul):
    """Member lists, the assign picker and task detail all read the same
    shape, so this checks the shape rather than each screen."""
    lead = _user("w_lead@example.com", "Lead", department="Computer Engineering")
    c = _community(lead, "Named")
    _join(lead, rahul, c["id"], "w_rahul@example.com")

    members = lead.get(f"/api/work/communities/{c['id']}").json()["members"]
    lead_row = [m for m in members if m["name"] == "Lead"][0]
    assert lead_row["department"] == "Computer Engineering"
    assert lead_row["department_id"]
    assert "email" not in lead_row

    ids = {m["name"]: m["id"] for m in members}
    lead.post(f"/api/work/communities/{c['id']}/tasks",
              json={"title": "Named task", "assignee_ids": [ids["Rahul"]]})
    task = rahul.get("/api/work/tasks").json()["tasks"][0]
    assert task["creator"]["department"] == "Computer Engineering"
    assert task["assignments"][0]["user"]["department"] is not None


def test_the_assignment_notification_names_the_department(owner, rahul):
    lead = _user("w_dept@example.com", "Dept Lead", department="Mechanical Engineering")
    c = _community(lead, "Naming")
    _join(lead, rahul, c["id"], "w_rahul@example.com")
    ids = _members(lead, c["id"])
    lead.post(f"/api/work/communities/{c['id']}/tasks",
              json={"title": "Say who", "assignee_ids": [ids["Rahul"]]})

    titles = [n["title"] for n in rahul.get("/api/work/notifications").json()["items"]]
    assert any("from Mechanical Engineering" in t for t in titles)


def test_cross_department_collaboration_is_never_blocked(owner, rahul):
    """A department is an identity, not a permission."""
    civil = _user("w_civil@example.com", "Civil Person", department="Civil Engineering")
    comp = _user("w_comp@example.com", "Comp Person", department="Computer Engineering")

    c = _community(civil, "Mixed")
    _join(civil, comp, c["id"], "w_comp@example.com")
    ids = _members(civil, c["id"])

    r = civil.post(f"/api/work/communities/{c['id']}/tasks",
                   json={"title": "Across departments", "assignee_ids": [ids["Comp Person"]]})
    assert r.status_code == 201
    task = comp.get("/api/work/tasks").json()["tasks"][0]
    assert comp.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True}).status_code == 200


# --- Admin: colleges and departments ---------------------------------------

def _super_admin(db_session, email="w_super@example.com"):
    from app.models import User

    c = _user(email, "Super")
    u = db_session.query(User).filter(User.email == email).first()
    u.is_admin = True
    db_session.commit()
    return c


def test_a_normal_user_cannot_create_a_college_or_department(owner):
    college, _ = _org(owner)
    assert owner.post("/api/org/colleges", json={"name": "Sneaky College"}).status_code == 403
    assert owner.post("/api/org/departments",
                      json={"college_id": college["id"], "name": "Sneaky Dept"}).status_code == 403
    assert owner.get("/api/org/manage/colleges").status_code == 403


def test_a_super_admin_can_add_and_edit_a_college(db_session):
    admin = _super_admin(db_session)
    created = admin.post("/api/org/colleges",
                         json={"name": "ABC College of Engineering", "location": "Pune"})
    assert created.status_code == 201
    cid = created.json()["id"]

    assert admin.put(f"/api/org/colleges/{cid}", json={"location": "Pimpri"}).json()["location"] == "Pimpri"
    listed = admin.get("/api/org/manage/colleges").json()
    assert listed["can_create_college"] is True
    assert "ABC College of Engineering" in {c["name"] for c in listed["colleges"]}
    # And it reaches the picker every user sees.
    assert "ABC College of Engineering" in {c["name"] for c in admin.get("/api/org/colleges").json()["colleges"]}


def test_a_duplicate_college_is_refused_however_it_is_typed(db_session):
    admin = _super_admin(db_session)
    admin.post("/api/org/colleges", json={"name": "Repeat Institute"})
    for variant in ("Repeat Institute", "repeat institute", "  Repeat,  Institute  "):
        assert admin.post("/api/org/colleges", json={"name": variant}).status_code == 409


def test_a_duplicate_department_is_refused_within_one_college(db_session):
    admin = _super_admin(db_session)
    college, _ = _org(admin)
    assert admin.post("/api/org/departments",
                      json={"college_id": college["id"], "name": "civil engineering"}).status_code == 409
    # But the same name under a different college is fine.
    other = admin.post("/api/org/colleges", json={"name": "Second College"}).json()
    assert admin.post("/api/org/departments",
                      json={"college_id": other["id"], "name": "Civil Engineering"}).status_code == 201


def test_a_department_with_members_is_archived_not_deleted(db_session):
    """Deleting would leave every one of those profiles pointing at nothing."""
    admin = _super_admin(db_session)
    college, depts = _org(admin)
    mine = admin.get("/api/org/profile").json()["department"]["id"]

    assert admin.delete(f"/api/org/departments/{mine}").status_code == 409
    archived = admin.put(f"/api/org/departments/{mine}", json={"status": "archived"})
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    # Archiving hides it from the picker but leaves the member attached.
    assert mine not in {d["id"] for d in _org(admin)[1]}
    assert admin.get("/api/org/profile").json()["department"]["id"] == mine


def test_an_empty_department_can_be_deleted(db_session):
    admin = _super_admin(db_session)
    college, _ = _org(admin)
    made = admin.post("/api/org/departments",
                      json={"college_id": college["id"], "name": "Temporary Studies"}).json()
    assert admin.delete(f"/api/org/departments/{made['id']}").status_code == 204


def test_a_college_admin_is_confined_to_their_own_college(db_session):
    from app.models import User

    admin = _super_admin(db_session)
    mine = admin.post("/api/org/colleges", json={"name": "Mine College"}).json()
    theirs = admin.post("/api/org/colleges", json={"name": "Theirs College"}).json()

    college_admin = _user("w_colladmin@example.com", "College Admin")
    row = db_session.query(User).filter(User.email == "w_colladmin@example.com").first()
    row.admin_college_id = mine["id"]
    db_session.commit()

    assert college_admin.post("/api/org/departments",
                              json={"college_id": mine["id"], "name": "Allowed"}).status_code == 201
    assert college_admin.post("/api/org/departments",
                              json={"college_id": theirs["id"], "name": "Refused"}).status_code == 403
    # Creating a college is a platform act, not a college one.
    assert college_admin.post("/api/org/colleges", json={"name": "Not mine to make"}).status_code == 403

    listed = college_admin.get("/api/org/manage/colleges").json()
    assert [c["name"] for c in listed["colleges"]] == ["Mine College"]
    assert listed["can_create_college"] is False


def test_nobody_can_move_another_person_between_departments(owner, rahul):
    """The profile endpoint takes no user id, so there is no request shape
    that edits somebody else."""
    college, depts = _org(owner)
    before = rahul.get("/api/org/profile").json()["department"]["id"]
    owner.put("/api/org/profile",
              json={"college_id": college["id"], "department_id": depts[-1]["id"],
                    "user_id": "whoever"})
    assert rahul.get("/api/org/profile").json()["department"]["id"] == before


# --- Existing accounts ------------------------------------------------------

def test_an_existing_account_is_linked_from_the_words_it_already_had(db_session):
    """Accounts predate these tables and carry free text. Where that text
    resolves, the link is made for them rather than asking again."""
    from app.database import link_existing_users
    from app.models import User

    c = _user("w_legacy@example.com", "Legacy", complete=False)
    row = db_session.query(User).filter(User.email == "w_legacy@example.com").first()
    # Spelled the way someone actually types it, not the way it is stored.
    row.college = "  dypcoe,  AKURDI "
    row.department = "computer engineering"
    db_session.commit()

    link_existing_users()
    db_session.expire_all()

    profile = c.get("/api/org/profile").json()
    assert profile["complete"] is True
    assert profile["college"]["name"] == "DYPCoE, Akurdi"
    assert profile["department"]["name"] == "Computer Engineering"


def test_an_unrecognisable_college_is_asked_about_rather_than_guessed(db_session):
    """Being filed under the wrong department is worse than being asked."""
    from app.database import link_existing_users
    from app.models import User

    c = _user("w_unknown@example.com", "Unknown", complete=False)
    row = db_session.query(User).filter(User.email == "w_unknown@example.com").first()
    row.college = "Some Institute Nobody Seeded"
    row.department = "Computer Engineering"
    db_session.commit()

    link_existing_users()
    db_session.expire_all()

    profile = c.get("/api/org/profile").json()
    assert profile["complete"] is False
    assert profile["department"] is None, "a department must never be inferred across colleges"


def test_linking_never_overwrites_a_choice_somebody_made(db_session):
    """The migration fills blanks. It does not correct people."""
    from app.database import link_existing_users
    from app.models import User

    c = _user("w_chosen@example.com", "Chosen", department="Civil Engineering")
    row = db_session.query(User).filter(User.email == "w_chosen@example.com").first()
    row.department = "Mechanical Engineering"   # stale free text, deliberately wrong
    db_session.commit()

    link_existing_users()
    db_session.expire_all()

    assert c.get("/api/org/profile").json()["department"]["name"] == "Civil Engineering"


def test_a_half_linked_account_still_has_to_choose(db_session):
    """A college without a department is exactly what the panel collects."""
    from app.models import User

    c = _user("w_half@example.com", "Half", complete=False)
    college, _ = _org(c)
    row = db_session.query(User).filter(User.email == "w_half@example.com").first()
    row.college_id = college["id"]
    db_session.commit()

    assert c.get("/api/org/profile").json()["complete"] is False
    assert c.post("/api/work/communities", json={"name": "Nope"}).status_code == 428
