"""Making someone the administrator of one college.

Two properties matter more than anything else here, and most of this file
exists to attack them.

A college administrator must not be able to become a super admin. Every route
that could get them there is tried: writing is_admin on themselves or on
anyone else, appointing a second administrator, resetting the password of a
super admin who happens to sit in their college.

A college administrator must not be able to reach outside their college. Every
endpoint that takes a user id is tried against a user in another college, and
against the users who belong to no college at all -- the new sign-ups, who are
nobody's to manage.

Appointment itself stays a super admin's act. It is the one call in the
application that hands out administrative power.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import User


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------
def _register(email, name="Person"):
    c = TestClient(app)
    c.post("/api/auth/register", json={"name": name, "email": email, "password": "password123"})
    return c


def _promote_super(db, email):
    """Directly, on purpose: there is no self-service way to become a super
    admin, and that is the property everything else here leans on."""
    user = db.query(User).filter(User.email == email).first()
    user.is_admin = True
    db.commit()
    return user


def _colleges(client):
    return client.get("/api/org/colleges").json()["colleges"]


def _departments(client, college_id):
    return client.get(f"/api/org/colleges/{college_id}/departments").json()["departments"]


def _join(client, college_id, dept_id):
    r = client.put("/api/org/profile", json={"college_id": college_id, "department_id": dept_id})
    assert r.status_code == 200, r.text
    return r


def _uid(db, email):
    return db.query(User).filter(User.email == email).first().id


@pytest.fixture
def world(db_session):
    """A super admin, two colleges, and members of each.

    Two colleges is the minimum that makes "their own college" mean anything;
    with one, every scoping test passes by accident.
    """
    boss = _register("boss@example.com", "Boss")
    _promote_super(db_session, "boss@example.com")

    first = _colleges(boss)[0]
    made = boss.post("/api/org/colleges", json={"name": "Second Institute", "location": "Pune"})
    assert made.status_code == 201, made.text
    second = made.json()
    dept = boss.post("/api/org/departments", json={"college_id": second["id"], "name": "Physics"})
    assert dept.status_code == 201, dept.text

    anita = _register("anita@example.com", "Anita")
    _join(anita, first["id"], _departments(anita, first["id"])[0]["id"])

    ravi = _register("ravi@example.com", "Ravi")
    _join(ravi, first["id"], _departments(ravi, first["id"])[0]["id"])

    outsider = _register("outsider@example.com", "Outsider")
    _join(outsider, second["id"], _departments(outsider, second["id"])[0]["id"])

    # Signed up, never chose a college. Belongs to nobody.
    drifter = _register("drifter@example.com", "Drifter")

    return {
        "boss": boss, "anita": anita, "ravi": ravi,
        "outsider": outsider, "drifter": drifter,
        "first": first, "second": second, "db": db_session,
    }


def _appoint(world, email="anita@example.com", college=None):
    uid = _uid(world["db"], email)
    college = college or world["first"]
    r = world["boss"].post(
        f"/api/admin/users/{uid}/college-admin", json={"college_id": college["id"]}
    )
    return r, uid


def _put_boss_in_the_college(world):
    """A super admin who is also a member of the college being administered --
    the case plain scoping would not stop."""
    boss = world["db"].query(User).filter(User.email == "boss@example.com").first()
    boss.college_id = world["first"]["id"]
    world["db"].commit()
    return boss


# ---------------------------------------------------------------------------
# Appointing
# ---------------------------------------------------------------------------
def test_a_super_admin_can_put_a_member_in_charge_of_their_college(world):
    r, _ = _appoint(world)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admin_college_id"] == world["first"]["id"]
    assert body["admin_college"] == world["first"]["name"]
    assert body["is_admin"] is False, "a college admin is not a super admin"


def test_the_appointee_gets_the_panel(world):
    assert world["anita"].get("/admin", follow_redirects=False).status_code in (302, 307)
    _appoint(world)
    assert world["anita"].get("/admin", follow_redirects=False).status_code == 200


def test_the_appointee_gets_a_link_to_it(world):
    """A panel reachable only by typing the address is not a panel."""
    assert 'href="/admin"' not in world["anita"].get("/dashboard").text
    _appoint(world)
    assert 'href="/admin"' in world["anita"].get("/dashboard").text


def test_standing_someone_down_takes_the_panel_away(world):
    _, uid = _appoint(world)
    r = world["boss"].delete(f"/api/admin/users/{uid}/college-admin")
    assert r.status_code == 200, r.text
    assert r.json()["admin_college_id"] is None
    assert world["anita"].get("/api/admin/users").status_code == 403
    assert world["anita"].get("/admin", follow_redirects=False).status_code in (302, 307)


def test_the_account_survives_being_stood_down(world):
    """Only the panel goes; they are still a member of their college."""
    _, uid = _appoint(world)
    world["boss"].delete(f"/api/admin/users/{uid}/college-admin")
    world["db"].expire_all()
    row = world["db"].get(User, uid)
    assert row.college_id == world["first"]["id"]
    assert row.is_active is True


def test_you_cannot_run_a_college_you_are_not_in(world):
    """The rule the whole feature rests on. An administrator appointed over
    another college would not appear in their own member list."""
    r, _ = _appoint(world, "outsider@example.com", world["first"])
    assert r.status_code == 400
    assert "different college" in r.json()["detail"]


def test_someone_with_no_college_cannot_be_appointed(world):
    r, _ = _appoint(world, "drifter@example.com")
    assert r.status_code == 400
    assert "not chosen a college" in r.json()["detail"]


def test_a_super_admin_is_not_demoted_into_a_college(world):
    r, _ = _appoint(world, "boss@example.com")
    assert r.status_code == 400
    assert "already a super admin" in r.json()["detail"]


def test_appointing_is_not_something_a_professor_can_do(world):
    uid = _uid(world["db"], "ravi@example.com")
    r = world["anita"].post(
        f"/api/admin/users/{uid}/college-admin", json={"college_id": world["first"]["id"]}
    )
    assert r.status_code == 403


def test_a_college_admin_cannot_appoint_a_second_one(world):
    """Otherwise the first appointment is the only one that ever needed a
    super admin, and the power spreads on its own."""
    _appoint(world)
    ravi_id = _uid(world["db"], "ravi@example.com")
    r = world["anita"].post(
        f"/api/admin/users/{ravi_id}/college-admin", json={"college_id": world["first"]["id"]}
    )
    assert r.status_code == 403
    world["db"].expire_all()
    assert world["db"].get(User, ravi_id).admin_college_id is None


def test_a_college_admin_cannot_stand_a_peer_down(world):
    _appoint(world)
    _appoint(world, "ravi@example.com")
    ravi_id = _uid(world["db"], "ravi@example.com")
    assert world["anita"].delete(f"/api/admin/users/{ravi_id}/college-admin").status_code == 403


# ---------------------------------------------------------------------------
# Seeing only their own college
# ---------------------------------------------------------------------------
def test_the_user_list_stops_at_the_college_boundary(world):
    _appoint(world)
    emails = {u["email"] for u in world["anita"].get("/api/admin/users").json()}
    assert "anita@example.com" in emails
    assert "ravi@example.com" in emails
    assert "outsider@example.com" not in emails, "another college"
    assert "drifter@example.com" not in emails, "no college, so nobody's to manage"
    assert "boss@example.com" not in emails, "the super admin is in no college"


def test_search_cannot_be_used_to_reach_past_it(world):
    """A filter narrows what is already permitted; it must not widen it."""
    _appoint(world)
    assert world["anita"].get("/api/admin/users?q=outsider").json() == []


def test_the_figures_are_the_colleges_own(world):
    _appoint(world)
    mine = world["anita"].get("/api/admin/stats").json()
    everything = world["boss"].get("/api/admin/stats").json()
    assert mine["total_users"] == 2, "Anita and Ravi"
    assert everything["total_users"] == 5
    assert mine["total_users"] < everything["total_users"]


def test_the_administrator_count_means_what_the_role_column_means(world):
    """It counted is_admin only, which told a college admin their college had
    no administrators while the list below showed them one -- themselves."""
    _appoint(world)
    assert world["anita"].get("/api/admin/stats").json()["admin_users"] == 1
    # For a super admin it still means super admins, platform-wide.
    assert world["boss"].get("/api/admin/stats").json()["admin_users"] == 1


def test_a_super_admin_still_sees_the_whole_platform(world):
    """The scoping must not have narrowed the panel it was added to."""
    emails = {u["email"] for u in world["boss"].get("/api/admin/users").json()}
    assert {"anita@example.com", "outsider@example.com", "drifter@example.com"} <= emails


@pytest.mark.parametrize("call", ["get", "events", "update", "password", "delete"])
def test_no_endpoint_reaches_another_college(world, call):
    """Every route that takes a user id, tried against an outsider. One of
    these forgetting to scope is the whole risk of the feature."""
    _appoint(world)
    anita, uid = world["anita"], _uid(world["db"], "outsider@example.com")

    if call == "get":
        r = anita.get(f"/api/admin/users/{uid}")
    elif call == "events":
        r = anita.get(f"/api/admin/users/{uid}/events")
    elif call == "update":
        r = anita.put(f"/api/admin/users/{uid}", json={"name": "Taken"})
    elif call == "password":
        r = anita.post(f"/api/admin/users/{uid}/reset-password", json={"new_password": "hijacked1"})
    else:
        r = anita.delete(f"/api/admin/users/{uid}")

    # Missing rather than forbidden: "you may not touch this" still confirms
    # the account exists, which turns the panel into a user enumerator.
    assert r.status_code == 404, f"{call} leaked across colleges: {r.status_code}"


def test_an_outsider_is_not_changed_by_the_attempt(world):
    _appoint(world)
    uid = _uid(world["db"], "outsider@example.com")
    world["anita"].put(f"/api/admin/users/{uid}", json={"name": "Taken"})
    world["db"].expire_all()
    assert world["db"].get(User, uid).name == "Outsider"


# ---------------------------------------------------------------------------
# Not becoming a super admin
# ---------------------------------------------------------------------------
def test_a_college_admin_cannot_promote_anyone(world):
    _appoint(world)
    ravi_id = _uid(world["db"], "ravi@example.com")
    r = world["anita"].put(f"/api/admin/users/{ravi_id}", json={"is_admin": True})
    assert r.status_code == 403
    world["db"].expire_all()
    assert world["db"].get(User, ravi_id).is_admin is False


def test_a_college_admin_cannot_promote_themselves(world):
    _, uid = _appoint(world)
    r = world["anita"].put(f"/api/admin/users/{uid}", json={"is_admin": True})
    assert r.status_code == 403
    world["db"].expire_all()
    assert world["db"].get(User, uid).is_admin is False


@pytest.mark.parametrize("field,value", [("is_admin", True), ("college", "Somewhere Else")])
def test_the_privileged_fields_are_refused_not_ignored(world, field, value):
    """Silently dropping the field would report success for a change that did
    not happen."""
    _appoint(world)
    ravi_id = _uid(world["db"], "ravi@example.com")
    r = world["anita"].put(f"/api/admin/users/{ravi_id}", json={field: value})
    assert r.status_code == 403
    assert field in r.json()["detail"]


def test_a_college_admin_cannot_move_someone_across_the_boundary(world):
    """The college is how scope is decided, so it cannot be something scope
    permits you to edit."""
    _appoint(world)
    ravi_id = _uid(world["db"], "ravi@example.com")
    world["anita"].put(f"/api/admin/users/{ravi_id}", json={"college": "Second Institute"})
    world["db"].expire_all()
    assert world["db"].get(User, ravi_id).college_id == world["first"]["id"]


def test_a_college_admin_cannot_reset_a_super_admins_password(world):
    """The takeover that scoping alone would not stop."""
    boss = _put_boss_in_the_college(world)
    _appoint(world)
    r = world["anita"].post(
        f"/api/admin/users/{boss.id}/reset-password", json={"new_password": "hijacked1"}
    )
    assert r.status_code == 403
    # And the password really is untouched: the super admin's session works.
    assert world["boss"].get("/api/admin/stats").status_code == 200


def test_a_college_admin_cannot_delete_a_super_admin_in_their_college(world):
    boss = _put_boss_in_the_college(world)
    _appoint(world)
    assert world["anita"].delete(f"/api/admin/users/{boss.id}").status_code == 403
    world["db"].expire_all()
    assert world["db"].query(User).filter(User.email == "boss@example.com").first() is not None


def test_a_college_admin_cannot_manage_another_college_admin(world):
    """Equal power, so neither gets to take over the other's account."""
    _appoint(world)
    _appoint(world, "ravi@example.com")
    ravi_id = _uid(world["db"], "ravi@example.com")
    r = world["anita"].post(
        f"/api/admin/users/{ravi_id}/reset-password", json={"new_password": "hijacked1"}
    )
    assert r.status_code == 403


def test_a_college_admin_cannot_create_an_administrator(world):
    _appoint(world)
    r = world["anita"].post("/api/admin/users", json={
        "name": "Planted", "email": "planted@example.com",
        "password": "password123", "is_admin": True,
    })
    assert r.status_code == 403
    assert world["db"].query(User).filter(User.email == "planted@example.com").first() is None


def test_a_user_created_by_a_college_admin_lands_in_that_college(world):
    """Otherwise the new account has no college and immediately falls outside
    the list of the administrator who just made it."""
    _appoint(world)
    r = world["anita"].post("/api/admin/users", json={
        "name": "New Lecturer", "email": "newbie@example.com", "password": "password123",
    })
    assert r.status_code == 201, r.text
    assert r.json()["is_admin"] is False
    row = world["db"].query(User).filter(User.email == "newbie@example.com").first()
    assert row.college_id == world["first"]["id"]
    listed = {u["email"] for u in world["anita"].get("/api/admin/users").json()}
    assert "newbie@example.com" in listed


# ---------------------------------------------------------------------------
# What the panel is told, so it does not offer actions that will be refused
# ---------------------------------------------------------------------------
def test_the_panel_is_told_which_rows_it_may_act_on(world):
    _put_boss_in_the_college(world)
    _appoint(world)
    rows = {u["email"]: u for u in world["anita"].get("/api/admin/users").json()}
    assert rows["ravi@example.com"]["manageable"] is True
    assert rows["boss@example.com"]["manageable"] is False
    assert rows["anita@example.com"]["manageable"] is False, "herself: an administrator"


def test_a_super_admin_may_act_on_every_row(world):
    assert all(u["manageable"] for u in world["boss"].get("/api/admin/users").json())


# ---------------------------------------------------------------------------
# Nothing that worked before stopped working
# ---------------------------------------------------------------------------
def test_a_professor_still_has_no_panel(world):
    for path in ("/api/admin/stats", "/api/admin/users"):
        assert world["ravi"].get(path).status_code == 403
    assert world["ravi"].get("/admin", follow_redirects=False).status_code in (302, 307)


def test_an_anonymous_caller_still_gets_401():
    assert TestClient(app).get("/api/admin/users").status_code == 401
