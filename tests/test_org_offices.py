"""Administrative posts sit in the same list as departments.

Principal and Registrar are not departments, but they answer the same question
a department does -- which part of the college someone belongs to -- and every
membership, filter and directory lookup in Work already runs on a department
id. So they are rows in the same table, told apart by `kind`, and shown under
their own heading.
"""
import pytest

from app.database import DEFAULT_DEPARTMENTS, DEFAULT_OFFICES
from app.models import User


@pytest.fixture
def admin_client(client, db_session):
    """Promoted directly in the database: there is deliberately no
    self-service route to becoming an administrator."""
    client.post("/api/auth/register",
                json={"name": "Admin", "email": "offices-admin@example.com",
                      "password": "adminpass123", "accepted_terms": True})
    user = db_session.query(User).filter(User.email == "offices-admin@example.com").first()
    user.is_admin = True
    db_session.commit()
    return client

EXPECTED_OFFICES = {
    "Principal",
    "Dean Administration",
    "Dean IQAC",
    "Dean Student Affairs",
    "Dean Academics",
    "Dean Industry Institute Interaction",
    "Dean Collaboration",
    "Registrar",
}


def _departments(client):
    college = client.get("/api/org/colleges").json()["colleges"][0]
    return client.get(f"/api/org/colleges/{college['id']}/departments").json()["departments"]


def test_every_requested_post_is_offered(auth_client):
    names = {d["name"] for d in _departments(auth_client)}
    assert EXPECTED_OFFICES <= names, f"missing: {EXPECTED_OFFICES - names}"


def test_the_posts_are_marked_as_posts(auth_client):
    by_name = {d["name"]: d for d in _departments(auth_client)}
    for office in EXPECTED_OFFICES:
        assert by_name[office]["kind"] == "office", office


def test_the_teaching_departments_are_still_there_and_still_academic(auth_client):
    by_name = {d["name"]: d for d in _departments(auth_client)}
    for name in DEFAULT_DEPARTMENTS:
        assert name in by_name, name
        assert by_name[name]["kind"] == "academic", name


def test_departments_come_before_posts(auth_client):
    """A list that opens with "Dean Academics" buries the answer almost
    everyone needs. Teaching departments first, posts after."""
    kinds = [d["kind"] for d in _departments(auth_client)]
    assert kinds == sorted(kinds, key=lambda k: k != "academic")


def test_a_post_can_be_chosen_as_a_work_profile(auth_client):
    """The whole point. Someone who is the Registrar must be able to complete
    their Work profile without pretending to be in Civil Engineering."""
    college = auth_client.get("/api/org/colleges").json()["colleges"][0]
    registrar = next(d for d in _departments(auth_client) if d["name"] == "Registrar")

    r = auth_client.put("/api/org/profile",
                        json={"college_id": college["id"], "department_id": registrar["id"]})
    assert r.status_code == 200, r.text

    profile = auth_client.get("/api/org/profile").json()
    assert profile["complete"] is True
    assert profile["department"]["name"] == "Registrar"


def test_a_post_holder_shows_their_post_wherever_people_are_named(auth_client):
    college = auth_client.get("/api/org/colleges").json()["colleges"][0]
    principal = next(d for d in _departments(auth_client) if d["name"] == "Principal")
    auth_client.put("/api/org/profile",
                    json={"college_id": college["id"], "department_id": principal["id"]})

    community = auth_client.post("/api/work/communities", json={"name": "Board"}).json()
    members = auth_client.get(f"/api/work/communities/{community['id']}").json()["members"]
    assert members[0]["department"] == "Principal"


def test_seeding_twice_does_not_duplicate_a_post(auth_client):
    """Idempotent by normalised name, like the departments beside them -- a
    redeploy must not leave two Registrars in the picker."""
    from app.database import seed_organisation

    before = len(_departments(auth_client))
    seed_organisation()
    seed_organisation()
    assert len(_departments(auth_client)) == before


def test_an_admin_can_add_a_further_post(admin_client):
    college = admin_client.get("/api/org/colleges").json()["colleges"][0]
    r = admin_client.post("/api/org/departments", json={
        "college_id": college["id"], "name": "Dean Research", "kind": "office"})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "office"


def test_a_new_department_is_academic_unless_told_otherwise(admin_client):
    """Every existing caller predates this field and must keep working."""
    college = admin_client.get("/api/org/colleges").json()["colleges"][0]
    r = admin_client.post("/api/org/departments",
                          json={"college_id": college["id"], "name": "Chemical Engineering"})
    assert r.status_code == 201
    assert r.json()["kind"] == "academic"


def test_an_unknown_kind_is_refused(admin_client):
    college = admin_client.get("/api/org/colleges").json()["colleges"][0]
    r = admin_client.post("/api/org/departments", json={
        "college_id": college["id"], "name": "Something", "kind": "faculty"})
    assert r.status_code == 422


@pytest.mark.parametrize("name", sorted(EXPECTED_OFFICES))
def test_each_post_is_spelled_the_way_it_was_asked_for(name):
    assert name in DEFAULT_OFFICES
