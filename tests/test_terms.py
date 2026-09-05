"""Agreeing to the terms, and the AI disclaimer that is the point of them.

The application sends what a professor types -- and the parts of their
timetable needed to answer it -- to a third-party model, and the answers can
be wrong about a class or a deadline. Saying so once, where it cannot be
missed, is the whole feature; the rest is making sure it cannot be skipped.

There are two ways to get an account, and only one of them passes through the
sign-up form. Google sign-in can be started from the *sign-in* page and
creates an account there, having shown nobody anything. So the form's checkbox
is not the enforcement -- the account is. A server-side check on every page
catches whatever the form did not.

Accounts that predate the terms are deliberately never asked. That is a
decision about not throwing a notice across the screen of every professor
mid-term, not an oversight, and moving TERMS_EFFECTIVE_AT back is how it would
be reversed.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import terms
from app.models import User

REGISTER = "/api/auth/register"
BASE_PAYLOAD = {"name": "Dr. New", "email": "newcomer@example.com",
                "password": "password123"}


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def test_the_terms_are_readable_without_an_account(client):
    """Terms you need an account to read are not terms you can agree to
    before making one."""
    resp = client.get("/terms", follow_redirects=False)
    assert resp.status_code == 200


def test_the_disclaimer_says_the_things_that_change_how_it_is_used(client):
    body = client.get("/terms").text.lower()
    for claim in ("can be wrong", "third-party", "confirm"):
        assert claim in body, f"the disclaimer has to say {claim!r}"


def test_the_disclaimer_comes_before_the_boilerplate(client):
    """It is the part with consequences for somebody planning a teaching week.
    Under the account and data sections it is decoration."""
    body = client.get("/terms").text
    assert body.index('id="ai"') < body.index('id="account"')


def test_crawlers_are_pointed_at_it(client):
    assert "Allow: /terms" in client.get("/robots.txt").text
    assert "/terms" in client.get("/sitemap.xml").text


# ---------------------------------------------------------------------------
# Signing up through the form
# ---------------------------------------------------------------------------
def test_an_account_cannot_be_created_without_agreeing(client):
    """Not merely unchecked on the form -- refused by the server, so posting
    straight to the endpoint cannot make an account that never agreed."""
    resp = client.post(REGISTER, json=BASE_PAYLOAD)
    assert resp.status_code == 422


def test_agreeing_falsely_is_refused_rather_than_ignored(client):
    resp = client.post(REGISTER, json={**BASE_PAYLOAD, "accepted_terms": False})
    assert resp.status_code == 422, "false must be a validation error, not a silent no"


def test_signing_up_records_what_was_agreed_to(client, db_session):
    resp = client.post(REGISTER, json={**BASE_PAYLOAD, "accepted_terms": True})
    assert resp.status_code == 201

    user = db_session.query(User).filter(User.email == BASE_PAYLOAD["email"]).one()
    assert user.terms_accepted_at is not None
    assert user.terms_version == terms.TERMS_VERSION, \
        "'they agreed' means nothing without recording what they agreed to"


def test_the_form_asks_before_it_submits(client):
    """The server refuses either way; this is what stops somebody being told
    off for missing a box they were never shown."""
    body = client.get("/register").text
    assert 'id="accept-terms"' in body
    assert "required" in body.split('id="accept-terms"')[1][:80]
    assert 'href="/terms"' in body


# ---------------------------------------------------------------------------
# The way in that never sees the form
# ---------------------------------------------------------------------------
def _google_style_account(db_session, **kw):
    """An account as the Google callback makes one: no password, and nothing
    recorded about the terms because nothing asked."""
    user = User(name="Dr. Google", email="google@example.com",
                password_hash=None, google_id="g-123", **kw)
    db_session.add(user)
    db_session.commit()
    return user


def test_an_account_that_never_saw_the_form_still_has_to_agree(db_session):
    user = _google_style_account(db_session)
    assert terms.needs_acceptance(user) is True


def test_agreeing_settles_it(db_session):
    user = _google_style_account(db_session)
    terms.record_acceptance(user)
    db_session.commit()
    assert terms.needs_acceptance(user) is False


def test_a_new_version_asks_again(db_session):
    """Recording that somebody agreed, without which terms, is not a record."""
    user = _google_style_account(db_session)
    terms.record_acceptance(user)
    user.terms_version = "an-older-version"
    db_session.commit()
    assert terms.needs_acceptance(user) is True


def test_accounts_that_predate_the_terms_are_left_alone(db_session):
    """Deliberate: the alternative is a notice across every professor's screen
    mid-term for something they did not sign up to."""
    user = _google_style_account(db_session)
    user.created_at = terms.TERMS_EFFECTIVE_AT - timedelta(days=1)
    db_session.commit()
    assert terms.needs_acceptance(user) is False


def test_a_naive_timestamp_does_not_raise(db_session):
    """SQLite hands back naive datetimes even from an aware column, and
    comparing one to an aware datetime raises rather than answering."""
    user = _google_style_account(db_session)
    user.created_at = datetime.now() + timedelta(days=1)   # naive, on purpose
    db_session.commit()
    assert terms.needs_acceptance(user) in (True, False)


def test_nobody_is_asked_when_there_is_nobody(client):
    """Signed-out visitors are not owed a question."""
    assert terms.needs_acceptance(None) is False
    assert client.get("/login", follow_redirects=False).status_code == 200


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def test_the_gate_is_on_the_router_and_not_on_each_page():
    """Fifteen handlers is fifteen chances to forget one, and a consent gate
    with one way round it is decoration."""
    src = Path("app/routers/pages.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(terms_gate)]" in src


@pytest.mark.parametrize("path", ["/terms", "/terms/accept", "/login", "/register", "/"])
def test_the_gate_leaves_a_way_to_answer_and_a_way_out(path):
    """Redirecting the terms page to the terms page is a closed loop."""
    from app.routers.pages import TERMS_EXEMPT
    assert path in TERMS_EXEMPT


def test_the_accept_page_needs_somebody_to_ask(client):
    resp = client.get("/terms/accept", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "/login" in resp.headers["location"]


def test_a_professor_who_has_agreed_is_not_asked_again(auth_client):
    """auth_client registers through the form, so it has already agreed."""
    resp = auth_client.get("/terms/accept", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "/dashboard" in resp.headers["location"]


def test_agreeing_is_its_own_endpoint(auth_client):
    """Not a flag on a settings update: agreeing is not a preference, and the
    only thing that should set it is somebody answering the question."""
    resp = auth_client.post("/api/auth/accept-terms")
    assert resp.status_code == 200
    assert resp.json()["version"] == terms.TERMS_VERSION


def test_agreeing_requires_being_signed_in(client):
    assert client.post("/api/auth/accept-terms").status_code == 401


def test_declining_is_possible(client):
    """An account somebody can neither use nor leave is a trap, and an
    agreement with no alternative is not one."""
    body = Path("app/templates/terms_accept.html").read_text(encoding="utf-8")
    assert 'id="terms-decline"' in body
    js = Path("app/static/js/terms.js").read_text(encoding="utf-8")
    assert "/api/auth/logout" in js


def test_the_answer_is_recorded_before_anyone_is_sent_on(client):
    """Navigating on the click and letting the request race behind it shows
    this page again on the next load, having apparently accepted nothing."""
    js = Path("app/static/js/terms.js").read_text(encoding="utf-8")
    body = js.split("addEventListener")[1]
    assert body.index("await apiFetch") < body.index("window.location.replace")


def test_an_unanswered_account_is_redirected_off_every_page(client, db_session):
    """The one that matters. Everything above checks the parts; this checks
    that a professor who arrived through Google actually meets the question
    instead of walking straight into the application.
    """
    from app.deps import COOKIE_NAME
    from app.security import create_access_token

    user = _google_style_account(db_session)
    client.cookies.set(COOKIE_NAME, create_access_token(user.id))

    for path in ("/dashboard", "/timetable", "/work", "/profile"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 307, f"{path} let an unanswered account through"
        assert resp.headers["location"] == "/terms/accept"

    # And the question itself is reachable, or it is a locked door.
    assert client.get("/terms/accept", follow_redirects=False).status_code == 200
    assert client.get("/terms", follow_redirects=False).status_code == 200


def test_answering_opens_the_application(client, db_session):
    from app.deps import COOKIE_NAME
    from app.security import create_access_token

    user = _google_style_account(db_session)
    client.cookies.set(COOKIE_NAME, create_access_token(user.id))
    assert client.get("/dashboard", follow_redirects=False).status_code == 307

    assert client.post("/api/auth/accept-terms").status_code == 200

    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 200, "answering has to actually let them in"


def test_a_rate_limited_answer_still_says_how_long_to_wait(client):
    """Found while wiring the gate, and older than it.

    The application's HTTPException handler rebuilt every error as a bare JSON
    body and dropped the headers it was raised with. The login limiter raises
    429 with a Retry-After; without it the answer is "slow down" with no
    indication of by how much, and a client cannot do anything sensible.
    """
    payload = {"email": "nobody@example.com", "password": "wrong-password"}
    last = None
    for _ in range(8):
        last = client.post("/api/auth/login", json=payload)
        if last.status_code == 429:
            break

    assert last.status_code == 429, "the limiter should have tripped"
    assert "retry-after" in {k.lower() for k in last.headers}, \
        "the header the limiter raises has to survive the handler"
    assert int(last.headers["retry-after"]) > 0
