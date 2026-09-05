"""Google AdSense, and the two ways it fails without saying so.

A third-party script blocked by the Content Security Policy does not raise
anything a reader would see. There is no error on the page, no ads, and
nothing to suggest the tag is present and being refused. So the tag and the
policy are checked together here: the tag alone is inert, and that is the
state this application was in before the policy was widened.

The second silent failure is ads.txt. Google will not pay out against
inventory it cannot verify belongs to the publisher claiming it; without the
file at the root, AdSense reports the account as "earnings at risk" and can
stop serving. That looks exactly like the ad code not working.
"""
import re
from pathlib import Path

import pytest

from app.security_headers import CSP

# The account the tag is for. Everything below has to agree with this: a
# publisher id that differs between the script and ads.txt is the classic way
# to have both present and still earn nothing.
PUBLISHER = "pub-7055956095625600"


@pytest.fixture(autouse=True)
def _quiet_db():
    """These read headers and templates; most open no session."""
    yield


# ---------------------------------------------------------------------------
# The tag
# ---------------------------------------------------------------------------
def test_the_tag_is_on_every_page(client, auth_client):
    """Signed out and signed in. It lives in base.html so it cannot be on one
    and not the other, and this is what says so."""
    for name, c, path in (("public", client, "/"), ("signed-in", auth_client, "/dashboard")):
        body = c.get(path).text
        assert "adsbygoogle.js" in body, f"missing on the {name} page"
        assert f"ca-{PUBLISHER}" in body, f"wrong or missing publisher on {name}"


def test_the_tag_is_in_the_head():
    """Where the loader expects to be. In the body it still runs, but the
    request starts later than it needs to."""
    html = Path("app/templates/base.html").read_text(encoding="utf-8")
    assert html.index("adsbygoogle.js") < html.index("</head>")


def test_the_tag_does_not_delay_the_page():
    """A synchronous third-party script in the head blocks first paint on
    somebody else's network."""
    html = Path("app/templates/base.html").read_text(encoding="utf-8")
    tag = html[html.index("<script async"):html.index("adsbygoogle.js") + 60]
    assert "async" in tag


# ---------------------------------------------------------------------------
# The policy that decides whether any of it runs
# ---------------------------------------------------------------------------
def _directive(name: str) -> str:
    for part in CSP.split(";"):
        part = part.strip()
        if part.startswith(name + " ") or part == name:
            return part
    raise AssertionError(f"{name} is not in the policy at all")


@pytest.mark.parametrize("directive", ["script-src", "img-src", "connect-src", "frame-src"])
def test_the_policy_admits_the_ad_network(directive):
    """All four, or it fails silently in a different place each time: no
    script, no creatives, no reporting, or no frames to draw in."""
    value = _directive(directive)
    assert any(host in value for host in
               ("googlesyndication.com", "doubleclick.net", "google.com")), \
        f"{directive} does not admit the ad network: {value!r}"


def test_ads_render_in_frames_so_frames_must_be_allowed():
    """Without frame-src the ads inherit default-src 'self' and every one of
    them is blocked, which looks exactly like having no ads."""
    assert "frame-src" in CSP
    assert "googlesyndication.com" in _directive("frame-src")


def test_admitting_advertising_did_not_open_the_doors_that_matter():
    """These are the directives that decide whether credentials and control of
    the page can leave. An ad network needs none of them, and widening them
    for one would be paying for advertising with the site's security."""
    assert _directive("form-action") == "form-action 'self'"
    assert _directive("base-uri") == "base-uri 'self'"
    assert _directive("frame-ancestors") == "frame-ancestors 'none'"
    assert _directive("object-src") == "object-src 'none'"


def test_no_wildcard_was_used_to_wave_it_all_through():
    """`script-src *` would have been one character of work and would admit
    anything at all."""
    assert " *" not in CSP.replace("https://*.", "")
    assert "'unsafe-eval'" not in CSP


def test_the_policy_is_actually_sent(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "pagead2.googlesyndication.com" in csp


# ---------------------------------------------------------------------------
# ads.txt
# ---------------------------------------------------------------------------
def test_ads_txt_is_served_from_the_root(client):
    """It has to be at the root. Anywhere else and Google does not look."""
    resp = client.get("/ads.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


def test_ads_txt_names_the_same_publisher_as_the_tag(client):
    """The classic way to have both and still earn nothing."""
    line = client.get("/ads.txt").text.strip()
    assert PUBLISHER in line
    fields = [f.strip() for f in line.split(",")]
    assert fields[0] == "google.com"
    assert fields[2] == "DIRECT", "the account is the publisher, not a reseller"


def test_ads_txt_is_reachable_without_answering_the_terms():
    """A crawler has no account and cannot agree to anything. Behind the gate
    it would be a redirect, and the file would read as missing."""
    from app.routers.pages import TERMS_EXEMPT
    assert "/ads.txt" in TERMS_EXEMPT


# ---------------------------------------------------------------------------
# Saying so
# ---------------------------------------------------------------------------
def test_the_terms_say_there_are_advertisements(client):
    """The terms were written the day before this, and describe what happens
    to a professor's data. Adding a third-party ad network without amending
    them would make them inaccurate on exactly the point they exist for."""
    body = client.get("/terms").text.lower()
    assert "advertis" in body
    assert "cookie" in body


def test_the_terms_are_clear_that_timetables_are_not_sold(client):
    body = client.get("/terms").text.lower()
    assert "not given to advertisers" in body or "not shared with google" in body


def test_every_template_with_its_own_head_carries_the_tag():
    """Written because one already did not.

    landing.html keeps its own <head> instead of extending base.html, so the
    tag in base.html never reached the one page anybody can visit without an
    account. Checking the two pages that exist today would pass again the
    moment somebody adds a third standalone template; this checks the rule.
    """
    from pathlib import Path

    missing = [
        p.name for p in Path("app/templates").glob("*.html")
        if "<head>" in p.read_text(encoding="utf-8")
        and "adsbygoogle" not in p.read_text(encoding="utf-8")
    ]
    assert not missing, f"these define a <head> with no ad tag in it: {missing}"
