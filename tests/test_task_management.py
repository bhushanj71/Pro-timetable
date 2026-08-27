"""
Changing a task after it has been assigned: who is on it, and what it says.

The rule underneath all of it: the people a task was given to answer for
their own row and nothing else. Only the person who assigned it, or an admin
of the community, may change the roster or the details -- otherwise anyone
could quietly write themselves out of work somebody is expecting.
"""
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.models import CommunityMember, CommunityRole, User


def _user(email, name):
    c = TestClient(app)
    c.post("/api/auth/register", json={"name": name, "email": email, "password": "password123"})
    college = c.get("/api/org/colleges").json()["colleges"][0]
    dept = c.get(f"/api/org/colleges/{college['id']}/departments").json()["departments"][0]
    c.put("/api/org/profile", json={"college_id": college["id"], "department_id": dept["id"]})
    return c


@pytest.fixture
def team(db_session):
    """A lead and three colleagues in one community."""
    lead = _user("tm_lead@example.com", "Lead")
    people = {n: _user(f"tm_{n.lower()}@example.com", n) for n in ("Rahul", "Priya", "Amit")}

    community = lead.post("/api/work/communities", json={"name": "Task Team"}).json()
    ids = {}
    for name in people:
        row = db_session.query(User).filter(User.email == f"tm_{name.lower()}@example.com").first()
        ids[name] = row.id
        db_session.add(CommunityMember(community_id=community["id"], user_id=row.id, role="member"))
    db_session.commit()
    return {"lead": lead, "clients": people, "ids": ids, "community": community}


def _task(team, assignees, title="Build the dashboard", **extra):
    body = {"title": title, "assignee_ids": [team["ids"][n] for n in assignees], **extra}
    return team["lead"].post(
        f"/api/work/communities/{team['community']['id']}/tasks", json=body).json()


def _names(task):
    return {a["user"]["name"] for a in task["assignments"]}


# --- Adding ----------------------------------------------------------------

def test_someone_added_later_starts_pending_like_everyone_else(team):
    task = _task(team, ["Rahul"])
    r = team["lead"].post(f"/api/work/tasks/{task['id']}/assignees",
                          json={"user_ids": [team["ids"]["Priya"]]})
    assert r.status_code == 201
    assert r.json()["added"] == 1

    updated = r.json()["task"]
    assert _names(updated) == {"Rahul", "Priya"}
    priya = [a for a in updated["assignments"] if a["user"]["name"] == "Priya"][0]
    assert priya["status"] == "pending", "adding somebody is still asking them, not telling them"


def test_adding_someone_already_on_the_task_changes_nothing(team):
    task = _task(team, ["Rahul"])
    r = team["lead"].post(f"/api/work/tasks/{task['id']}/assignees",
                          json={"user_ids": [team["ids"]["Rahul"]]})
    assert r.status_code == 201
    assert r.json()["added"] == 0
    assert len(r.json()["task"]["assignments"]) == 1


def test_a_task_cannot_reach_outside_its_community(team, db_session):
    outsider = _user("tm_outsider@example.com", "Outsider")
    outsider_id = db_session.query(User).filter(User.email == "tm_outsider@example.com").first().id

    task = _task(team, ["Rahul"])
    r = team["lead"].post(f"/api/work/tasks/{task['id']}/assignees",
                          json={"user_ids": [outsider_id]})
    assert r.status_code == 400
    assert "isn't in this community" in r.json()["detail"]


def test_the_person_added_is_told(team):
    task = _task(team, ["Rahul"])
    team["lead"].post(f"/api/work/tasks/{task['id']}/assignees",
                      json={"user_ids": [team["ids"]["Priya"]]})
    titles = [n["title"] for n in team["clients"]["Priya"].get("/api/work/notifications").json()["items"]]
    assert any("Build the dashboard" in t for t in titles)


# --- Removing --------------------------------------------------------------

def test_what_a_removal_costs_can_be_asked_before_doing_it(team):
    """An assignment carries a status, a percentage and a history. Saying so
    first is the difference between a decision and a surprise."""
    task = _task(team, ["Rahul"])
    rahul = team["clients"]["Rahul"]
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 60, "note": "half"})

    cost = team["lead"].get(
        f"/api/work/tasks/{task['id']}/assignees/{team['ids']['Rahul']}/removal-cost").json()
    assert cost["name"] == "Rahul"
    assert cost["progress"] == 60
    assert cost["accepted"] is True
    assert cost["updates"] >= 1


def test_removing_someone_takes_them_off_and_tells_them(team):
    task = _task(team, ["Rahul", "Priya"])
    r = team["lead"].delete(f"/api/work/tasks/{task['id']}/assignees/{team['ids']['Rahul']}")
    assert r.status_code == 200
    assert _names(r.json()["task"]) == {"Priya"}

    kinds = [n["kind"] for n in team["clients"]["Rahul"].get("/api/work/notifications").json()["items"]]
    assert "task_unassigned" in kinds, "work vanishing silently is worse than being told"


def test_a_removed_person_no_longer_counts_towards_progress(team):
    """The overall figure averages the people actually carrying the task."""
    task = _task(team, ["Rahul", "Priya"])
    for name in ("Rahul", "Priya"):
        team["clients"][name].post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    team["clients"]["Rahul"].put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 100})
    team["clients"]["Priya"].put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 0})

    before = team["lead"].get(f"/api/work/tasks/{task['id']}").json()["progress"]
    assert before["overall"] == 50

    after = team["lead"].delete(
        f"/api/work/tasks/{task['id']}/assignees/{team['ids']['Priya']}").json()["task"]
    assert after["progress"]["overall"] == 100
    assert after["progress"]["accepted"] == 1


def test_removing_someone_not_on_the_task_is_a_404(team):
    task = _task(team, ["Rahul"])
    assert team["lead"].delete(
        f"/api/work/tasks/{task['id']}/assignees/{team['ids']['Amit']}").status_code == 404


# --- Reassigning -----------------------------------------------------------

def test_reassigning_moves_the_place_and_tells_both_people(team):
    task = _task(team, ["Rahul"])
    r = team["lead"].post(f"/api/work/tasks/{task['id']}/reassign",
                          json={"from_user_id": team["ids"]["Rahul"],
                                "to_user_id": team["ids"]["Priya"]})
    assert r.status_code == 200
    assert _names(r.json()["task"]) == {"Priya"}

    rahul_kinds = [n["kind"] for n in team["clients"]["Rahul"].get("/api/work/notifications").json()["items"]]
    priya_kinds = [n["kind"] for n in team["clients"]["Priya"].get("/api/work/notifications").json()["items"]]
    assert "task_unassigned" in rahul_kinds
    assert "task_assigned" in priya_kinds


def test_the_new_person_starts_at_zero_and_pending(team):
    """Progress belongs to whoever did the work. Inheriting a number nobody
    earned would make the overall figure a fiction."""
    task = _task(team, ["Rahul"])
    rahul = team["clients"]["Rahul"]
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    rahul.put(f"/api/work/tasks/{task['id']}/progress", json={"progress": 80})

    after = team["lead"].post(f"/api/work/tasks/{task['id']}/reassign",
                              json={"from_user_id": team["ids"]["Rahul"],
                                    "to_user_id": team["ids"]["Priya"]}).json()["task"]
    priya = after["assignments"][0]
    assert priya["user"]["name"] == "Priya"
    assert priya["progress"] == 0
    assert priya["status"] == "pending"


def test_reassigning_to_someone_already_on_the_task_is_refused(team):
    task = _task(team, ["Rahul", "Priya"])
    r = team["lead"].post(f"/api/work/tasks/{task['id']}/reassign",
                          json={"from_user_id": team["ids"]["Rahul"],
                                "to_user_id": team["ids"]["Priya"]})
    assert r.status_code == 409


# --- Editing the task ------------------------------------------------------

def test_updating_a_task_reports_only_what_actually_changed(team):
    """A form that posts every field must not announce five changes when one
    was made, so before and after are compared rather than the payload
    trusted."""
    task = _task(team, ["Rahul"], priority="medium")
    r = team["lead"].put(f"/api/work/tasks/{task['id']}", json={
        "title": "Build the dashboard",       # unchanged
        "priority": "urgent",                 # changed
    })
    assert r.status_code == 200
    assert r.json()["changed"] == ["priority changed"]
    assert r.json()["task"]["priority"] == "urgent"


def test_updating_nothing_notifies_nobody(team):
    task = _task(team, ["Rahul"])
    before = len(team["clients"]["Rahul"].get("/api/work/notifications").json()["items"])
    r = team["lead"].put(f"/api/work/tasks/{task['id']}", json={"title": "Build the dashboard"})
    assert r.json()["changed"] == []
    after = len(team["clients"]["Rahul"].get("/api/work/notifications").json()["items"])
    assert after == before


def test_the_people_carrying_a_task_are_told_it_moved(team):
    task = _task(team, ["Rahul"])
    team["clients"]["Rahul"].post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})
    due = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()

    team["lead"].put(f"/api/work/tasks/{task['id']}", json={"due_date": due, "priority": "high"})
    items = team["clients"]["Rahul"].get("/api/work/notifications").json()["items"]
    updated = [n for n in items if n["kind"] == "task_updated"]
    assert updated, "someone carrying the task has to hear that its deadline moved"
    assert "due date changed" in updated[0]["body"]


def test_someone_who_declined_is_not_told_about_later_edits(team):
    """They said no. It is no longer their business."""
    task = _task(team, ["Rahul", "Priya"])
    team["clients"]["Rahul"].post(f"/api/work/tasks/{task['id']}/respond", json={"accept": False})
    team["lead"].put(f"/api/work/tasks/{task['id']}", json={"priority": "urgent"})

    kinds = [n["kind"] for n in team["clients"]["Rahul"].get("/api/work/notifications").json()["items"]]
    assert "task_updated" not in kinds


def test_an_unknown_priority_is_refused(team):
    task = _task(team, ["Rahul"])
    assert team["lead"].put(f"/api/work/tasks/{task['id']}",
                            json={"priority": "catastrophic"}).status_code == 400


# --- Who may do any of this ------------------------------------------------

def test_an_assignee_cannot_change_the_roster_or_the_task(team):
    """They answer for their own row and nothing else."""
    task = _task(team, ["Rahul", "Priya"])
    rahul = team["clients"]["Rahul"]

    assert rahul.post(f"/api/work/tasks/{task['id']}/assignees",
                      json={"user_ids": [team["ids"]["Amit"]]}).status_code == 403
    assert rahul.delete(
        f"/api/work/tasks/{task['id']}/assignees/{team['ids']['Priya']}").status_code == 403
    assert rahul.post(f"/api/work/tasks/{task['id']}/reassign",
                      json={"from_user_id": team["ids"]["Priya"],
                            "to_user_id": team["ids"]["Amit"]}).status_code == 403
    assert rahul.put(f"/api/work/tasks/{task['id']}", json={"priority": "low"}).status_code == 403
    # And nothing moved.
    assert _names(team["lead"].get(f"/api/work/tasks/{task['id']}").json()) == {"Rahul", "Priya"}


def test_an_assignee_cannot_remove_themselves_from_work_they_accepted(team):
    task = _task(team, ["Rahul"])
    rahul = team["clients"]["Rahul"]
    rahul.post(f"/api/work/tasks/{task['id']}/respond", json={"accept": True})

    assert rahul.delete(
        f"/api/work/tasks/{task['id']}/assignees/{team['ids']['Rahul']}").status_code == 403


def test_a_community_admin_can_manage_a_task_they_did_not_create(team, db_session):
    task = _task(team, ["Rahul"])
    row = db_session.query(CommunityMember).filter(
        CommunityMember.community_id == team["community"]["id"],
        CommunityMember.user_id == team["ids"]["Amit"],
    ).first()
    row.role = CommunityRole.ADMIN.value
    db_session.commit()

    amit = team["clients"]["Amit"]
    assert amit.get(f"/api/work/tasks/{task['id']}").json()["can_manage"] is True
    assert amit.put(f"/api/work/tasks/{task['id']}", json={"priority": "high"}).status_code == 200


def test_can_manage_tells_the_ui_the_truth_for_a_plain_assignee(team):
    task = _task(team, ["Rahul"])
    assert team["clients"]["Rahul"].get(f"/api/work/tasks/{task['id']}").json()["can_manage"] is False
    assert team["lead"].get(f"/api/work/tasks/{task['id']}").json()["can_manage"] is True
