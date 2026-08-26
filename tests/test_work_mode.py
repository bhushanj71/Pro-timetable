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
