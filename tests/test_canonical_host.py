"""One hostname, one scheme.

A site reachable at several addresses is several sites. The session cookie is
host-only, so a professor who signs in on www.profschedule.org and follows a
link to profschedule.org is signed out with nothing on screen to explain it;
a search engine treats the two as competitors; and an OAuth redirect URI
matches exactly one of them.

The redirect is off unless a canonical host is configured, which is what keeps
localhost and preview deployments working normally.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def site(monkeypatch):
    """The application as it runs on the real domain.

    The settings cache is cleared rather than the module reimported. Reloading
    app.main builds a second application object, and the fixtures that give
    tests a database are attached to the first -- which is a way to spend an
    afternoon on a failure that has nothing to do with the code under test.
    """
    monkeypatch.setenv("CANONICAL_HOST", "www.profschedule.org")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://www.profschedule.org")

    from app.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _get(site, path, host="www.profschedule.org", proto="https"):
    return site.get(path, headers={"host": host, "x-forwarded-proto": proto},
                    follow_redirects=False)


@pytest.mark.parametrize("host,proto", [
    ("profschedule.org", "https"),          # the apex
    ("profschedule-ai.onrender.com", "https"),  # the platform's own name
    ("www.profschedule.org", "http"),       # right host, wrong scheme
    ("profschedule.org", "http"),           # both wrong
])
def test_every_other_spelling_lands_on_the_canonical_one(site, host, proto):
    r = _get(site, "/dashboard", host=host, proto=proto)
    assert r.status_code == 301, "permanent, and worth caching"
    assert r.headers["location"] == "https://www.profschedule.org/dashboard"


def test_the_path_and_query_survive_the_redirect(site):
    """A shared link is usually a link to something, not to the front page."""
    r = _get(site, "/work/history/completed?from=email", host="profschedule.org")
    assert r.headers["location"] == \
        "https://www.profschedule.org/work/history/completed?from=email"


def test_the_scheme_and_the_host_are_fixed_in_one_hop(site):
    """Two redirects is two round trips before anything renders."""
    r = _get(site, "/", host="profschedule.org", proto="http")
    assert r.status_code == 301
    assert r.headers["location"].startswith("https://www.profschedule.org")


def test_the_canonical_address_is_not_redirected(site):
    """The obvious way to write this is a loop."""
    assert _get(site, "/login").status_code == 200


def test_the_health_check_is_never_redirected(site):
    """The platform makes it against its own hostname. Redirect that away and
    the deployment is marked unhealthy and rolled back."""
    r = _get(site, "/api/health", host="profschedule-ai.onrender.com")
    assert r.status_code == 200


def test_without_a_canonical_host_nothing_is_redirected(client):
    """Which is how localhost and preview deployments stay usable."""
    r = client.get("/login", headers={"host": "anything.example"},
                   follow_redirects=False)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# What the site says about itself
# ---------------------------------------------------------------------------
def test_the_front_page_names_its_own_address(site):
    """Without a canonical, the apex, the www and the platform hostname are
    three sites sharing none of each other's standing."""
    body = _get(site, "/").text
    assert '<link rel="canonical" href="https://www.profschedule.org/">' in body


def test_a_shared_link_previews_as_something(site):
    body = _get(site, "/").text
    for tag in ("og:title", "og:description", "og:image", "og:url",
                "twitter:card"):
        assert tag in body, f"missing {tag}"
    assert 'content="https://www.profschedule.org/' in body, "absolute, not relative"


def test_crawlers_are_pointed_at_the_public_pages_only(site):
    """Not as a security measure -- the API enforces that and a robots file is
    a request, not a rule -- but because a crawler following /dashboard gets a
    login redirect and indexes that instead."""
    body = _get(site, "/robots.txt").text
    assert "Disallow: /dashboard" in body
    assert "Disallow: /api/" in body
    assert "Sitemap: https://www.profschedule.org/sitemap.xml" in body


def test_the_sitemap_lists_absolute_public_urls(site):
    body = _get(site, "/sitemap.xml").text
    assert "<loc>https://www.profschedule.org/</loc>" in body
    assert "/dashboard" not in body, "nothing behind the login belongs here"


def test_absolute_links_the_application_sends_out_use_the_domain(site):
    """The calendar feed and the OAuth redirect are built from this, and a
    stale value there is a link that goes to the wrong place entirely."""
    from app.config import get_settings
    assert get_settings().PUBLIC_BASE_URL == "https://www.profschedule.org"


def test_no_stale_hostname_is_left_in_the_source():
    """profschedule.ai was hard-coded in the Google calendar payload and in the
    UID of every exported calendar event -- a domain that is not ours.

    Prose is skipped deliberately -- a comment explaining why the value changed
    has to name the old one, and a check that forbids saying so pushes the
    explanation out of the code. So this looks at values the program actually
    uses: string literals that are not docstrings.
    """
    import ast
    from pathlib import Path

    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = node.body[0] if node.body else None
                if isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant):
                    docstrings.add(id(doc.value))

        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                assert "profschedule.ai" not in node.value, \
                    f"{path}:{getattr(node, 'lineno', '?')}"
