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

def _set_college(client, college, department=None):
    client.put("/api/auth/me", json={"college": college, "department": department})


def test_the_directory_lists_colleagues_at_the_same_college(owner, rahul, amit):
    _set_college(owner, "DY Patil COE", "Computer")
    _set_college(rahul, "DY Patil COE", "Mechanical")
    _set_college(amit, "Some Other College", "Computer")

    c = _community(owner, "Faculty Room")
    body = owner.get(f"/api/work/communities/{c['id']}/directory").json()

    names = {p["name"] for p in body["people"]}
    assert "Rahul" in names
    assert "Amit" not in names, "the directory must not reach beyond one college"
    assert body["college"] == "DY Patil COE"
    # The email is never returned: it is enough to invite by, and a browsable
    # list of addresses is a directory to harvest.
    assert all("email" not in p for p in body["people"])


def test_a_college_that_is_written_differently_still_matches(owner, rahul):
    _set_college(owner, "DY Patil COE")
    _set_college(rahul, "  dy patil coe ")
    c = _community(owner, "Faculty Room")
    people = owner.get(f"/api/work/communities/{c['id']}/directory").json()["people"]
    assert {p["name"] for p in people} == {"Rahul"}


def test_a_viewer_with_no_college_gets_nobody_not_everybody(owner, rahul):
    """The failure that matters. If the check were written as an equality
    against viewer.college, a blank college would match every other account
    that also left it blank."""
    c = _community(owner, "Faculty Room")
    body = owner.get(f"/api/work/communities/{c['id']}/directory").json()
    assert body["people"] == []
    assert body["college"] is None
    assert "Settings" in body["reason"]


def test_the_directory_says_who_is_already_in(owner, rahul):
    _set_college(owner, "Same Place")
    _set_college(rahul, "Same Place")
    c = _community(owner, "Faculty Room")
    _join(owner, rahul, c["id"], "w_rahul@example.com")

    rahul_row = [p for p in owner.get(
        f"/api/work/communities/{c['id']}/directory").json()["people"] if p["name"] == "Rahul"][0]
    assert rahul_row["member"] is True


def test_the_directory_can_be_searched_by_name_and_department(owner, rahul, amit):
    _set_college(owner, "Same Place")
    _set_college(rahul, "Same Place", "Mechanical")
    _set_college(amit, "Same Place", "Computer")
    c = _community(owner, "Faculty Room")

    url = f"/api/work/communities/{c['id']}/directory"
    assert {p["name"] for p in owner.get(url, params={"q": "rah"}).json()["people"]} == {"Rahul"}
    assert {p["name"] for p in owner.get(url, params={"q": "Computer"}).json()["people"]} == {"Amit"}
    assert owner.get(url, params={"q": "nobody"}).json()["people"] == []


def test_only_admins_can_browse_the_directory(owner, rahul, amit):
    _set_college(owner, "Same Place")
    _set_college(rahul, "Same Place")
    c = _community(owner, "Faculty Room")
    _join(owner, rahul, c["id"], "w_rahul@example.com")

    assert rahul.get(f"/api/work/communities/{c['id']}/directory").status_code == 403
    assert amit.get(f"/api/work/communities/{c['id']}/directory").status_code == 404


# --- Profile options -------------------------------------------------------

def _opts(client):
    return client.get("/api/auth/profile-options").json()


def test_department_options_are_offered(owner):
    body = _opts(owner)
    assert "Computer Engineering" in body["departments"]
    assert "Mechanical Engineering" in body["departments"]
    assert len(body["departments"]) > 10


def test_college_suggestions_come_only_from_the_same_email_domain(owner):
    """The list exists so colleagues converge on one spelling. Built from
    every account instead, it would publish which institutions use this app
    and hand anyone the exact string needed to aim a community directory at
    one of them."""
    inside = _user("staff@dypcoe.ac.in", "Inside")
    inside.put("/api/auth/me", json={"college": "DY Patil COE"})
    outside = _user("someone@otherplace.edu", "Outside")
    outside.put("/api/auth/me", json={"college": "Other Place Institute"})

    peer = _user("peer@dypcoe.ac.in", "Peer")
    body = _opts(peer)
    assert body["college_domain"] == "dypcoe.ac.in"
    assert body["colleges"] == ["DY Patil COE"]
    assert "Other Place Institute" not in body["colleges"]


def test_a_public_inbox_gets_no_college_suggestions(owner):
    """A gmail address says nothing about where someone works, so treating
    gmail as an institution would make every user a colleague."""
    gmail_user = _user("someone@gmail.com", "Gmail Person")
    gmail_user.put("/api/auth/me", json={"college": "Somewhere"})
    other_gmail = _user("another@gmail.com", "Other Gmail")

    body = _opts(other_gmail)
    assert body["college_domain"] is None
    assert body["colleges"] == []


def test_one_college_spelled_two_ways_is_listed_once(owner):
    a = _user("a@samecollege.ac.in", "A")
    a.put("/api/auth/me", json={"college": "Same College"})
    b = _user("b@samecollege.ac.in", "B")
    b.put("/api/auth/me", json={"college": "  same college "})

    assert len(_opts(_user("c@samecollege.ac.in", "C"))["colleges"]) == 1


def test_a_typed_college_snaps_to_the_spelling_a_colleague_already_uses(owner):
    a = _user("a@snapping.ac.in", "A")
    a.put("/api/auth/me", json={"college": "Snapping Institute"})

    b = _user("b@snapping.ac.in", "B")
    saved = b.put("/api/auth/me", json={"college": "  snapping institute  "}).json()
    assert saved["college"] == "Snapping Institute"


def test_a_college_is_not_snapped_across_different_domains(owner):
    a = _user("a@one.ac.in", "A")
    a.put("/api/auth/me", json={"college": "Shared Name"})
    b = _user("b@two.ac.in", "B")
    saved = b.put("/api/auth/me", json={"college": "shared name"}).json()
    assert saved["college"] == "shared name", "a different institution keeps its own value"


def test_a_department_outside_the_list_is_still_accepted(owner):
    """The dropdown has an Other escape, and the server must not police it."""
    saved = owner.put("/api/auth/me", json={"department": "Marine Engineering"}).json()
    assert saved["department"] == "Marine Engineering"
    assert _opts(owner)["current"]["department"] == "Marine Engineering"


def test_a_domain_carrying_a_like_wildcard_is_rejected(owner):
    """The domain is interpolated into a LIKE pattern. A % in it would widen
    the match to every other institution."""
    from app.routers.auth import email_domain

    assert email_domain("someone@a%.ac.in") is None
    assert email_domain("someone@a_b.ac.in") is None
    assert email_domain("someone@real.ac.in") == "real.ac.in"
    assert email_domain("nonsense") is None
    assert email_domain("someone@gmail.com") is None
