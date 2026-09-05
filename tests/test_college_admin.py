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
    c.post("/api/auth/register", json={"name": name, "email": email, "password": "password123", "accepted_terms": True})
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


# ---------------------------------------------------------------------------
# Browsing who is enrolled where
#
# One endpoint serves both administrators: a super admin picks the college and
# may narrow by department, a college admin has the college settled for them
# and picks the department.
# ---------------------------------------------------------------------------
def _members(client, **params):
    return client.get("/api/org/manage/members", params=params).json()


def test_a_super_admin_picks_the_college_and_gets_its_members(world):
    first = _members(world["boss"], college_id=world["first"]["id"])["members"]
    second = _members(world["boss"], college_id=world["second"]["id"])["members"]
    assert {m["name"] for m in first} == {"Anita", "Ravi"}
    assert {m["name"] for m in second} == {"Outsider"}


def test_a_super_admin_can_narrow_to_one_department(world):
    college = world["first"]["id"]
    dept = _departments(world["boss"], college)[0]["id"]
    rows = _members(world["boss"], college_id=college, department_id=dept)["members"]
    assert rows, "the department the members joined should not come back empty"
    assert all(m["department_id"] == dept for m in rows)


def test_a_college_admin_gets_their_own_college_without_asking(world):
    """They have no college to pick, so the endpoint decides for them."""
    _appoint(world)
    rows = _members(world["anita"])["members"]
    assert {m["name"] for m in rows} == {"Anita", "Ravi"}


def test_a_college_admin_asking_for_another_college_still_gets_their_own(world):
    """The filter is the caller's request; the scope is not."""
    _appoint(world)
    rows = _members(world["anita"], college_id=world["second"]["id"])["members"]
    assert {m["name"] for m in rows} == {"Anita", "Ravi"}
    assert "Outsider" not in {m["name"] for m in rows}


def test_a_department_from_another_college_reveals_nobody(world):
    """The department filter is applied on top of the forced college, so a
    borrowed id narrows to nothing rather than reaching across."""
    _appoint(world)
    other_dept = _departments(world["boss"], world["second"]["id"])[0]["id"]
    assert _members(world["anita"], department_id=other_dept)["members"] == []


def test_a_college_admin_picks_a_department_within_their_college(world):
    _appoint(world)
    dept = _departments(world["boss"], world["first"]["id"])[0]["id"]
    rows = _members(world["anita"], department_id=dept)["members"]
    assert rows
    assert all(m["department_id"] == dept for m in rows)


def test_a_professor_cannot_browse_members_at_all(world):
    assert world["ravi"].get("/api/org/manage/members").status_code == 403


def test_the_member_list_still_hands_out_no_email(world):
    """The directory rule Work has always had. Account details, email
    included, live in the admin user table, which is scoped separately."""
    _appoint(world)
    for m in _members(world["anita"])["members"]:
        assert "email" not in m


def test_a_short_list_is_not_reported_as_cut(world):
    _appoint(world)
    assert _members(world["anita"])["truncated"] is False


def test_managing_people_is_a_page_of_its_own(world):
    """It was a table on the admin overview and a members dialog above it.
    Being a real URL is what the back button and a linkable filtered list are
    built on."""
    page = world["boss"].get("/admin/members")
    assert page.status_code == 200
    body = page.text
    assert 'id="ad-user-tbody"' in body, "the user table lives here now"
    assert 'id="ad-members-back"' in body, "and it has a way back"

    overview = world["boss"].get("/admin").text
    assert 'id="ad-user-tbody"' not in overview, "not in two places at once"
    assert 'href="/admin/members"' in overview, "but reachable from the overview"


def test_the_members_page_is_administrators_only(world):
    assert world["ravi"].get("/admin/members", follow_redirects=False).status_code in (302, 307)
    _appoint(world)
    assert world["anita"].get("/admin/members", follow_redirects=False).status_code == 200


def test_a_college_admin_is_not_offered_a_college_to_choose(world):
    """They have exactly one, and the API decides it for them regardless."""
    _appoint(world)
    assert 'id="ad-f-college"' not in world["anita"].get("/admin/members").text
    assert 'id="ad-f-college"' in world["boss"].get("/admin/members").text


def test_a_college_admin_is_not_offered_a_role_to_set(world):
    """The server refuses is_admin from them, so the form does not ask."""
    _appoint(world)
    assert 'id="ad-is-admin"' not in world["anita"].get("/admin/members").text
    assert 'id="ad-is-admin"' in world["boss"].get("/admin/members").text


def _admin_users(client, **params):
    r = client.get("/api/admin/users", params=params)
    assert r.status_code == 200, r.text
    return {u["email"] for u in r.json()}


def test_the_table_filters_by_college(world):
    assert _admin_users(world["boss"], college_id=world["first"]["id"]) == {
        "anita@example.com", "ravi@example.com"}
    assert _admin_users(world["boss"], college_id=world["second"]["id"]) == {
        "outsider@example.com"}


def test_the_table_filters_by_department(world):
    college = world["first"]["id"]
    dept = _departments(world["boss"], college)[0]["id"]
    found = _admin_users(world["boss"], college_id=college, department_id=dept)
    assert found, "the department they joined should not be empty"
    assert found <= {"anita@example.com", "ravi@example.com"}


def test_the_accounts_with_no_college_can_still_be_reached(world):
    """Most accounts on a young deployment have not chosen a college. No
    college row would ever list them, so without a way to ask for them they
    would be beyond management entirely."""
    assert _admin_users(world["boss"], college_id="none") == {
        "boss@example.com", "drifter@example.com"}


def test_a_filter_cannot_widen_a_college_admins_scope(world):
    """The filters narrow what is already permitted. Every one of them, tried
    against another college."""
    _appoint(world)
    mine = {"anita@example.com", "ravi@example.com"}
    other_dept = _departments(world["boss"], world["second"]["id"])[0]["id"]

    assert _admin_users(world["anita"]) == mine
    assert _admin_users(world["anita"], college_id=world["second"]["id"]) == set()
    assert _admin_users(world["anita"], department_id=other_dept) == set()
    # And the sentinel does not become a hole either.
    assert _admin_users(world["anita"], college_id="none") == set()


def test_a_cut_list_says_so(world, db_session):
    """An administrator who reads a count off a truncated page has been given
    a wrong answer, not a partial one."""
    from app.models import User as U

    college = world["first"]["id"]
    dept = world["db"].query(U).filter(U.email == "anita@example.com").first().department_id
    payload = _members(world["boss"], college_id=college)
    limit = payload["limit"]
    for i in range(limit):
        db_session.add(U(name=f"Crowd {i:03d}", email=f"crowd{i}@example.com",
                         college_id=college, department_id=dept))
    db_session.commit()

    payload = _members(world["boss"], college_id=college)
    assert payload["truncated"] is True
    assert len(payload["members"]) == limit
