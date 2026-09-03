"""Work is a module, not a page, and the nav should say so.

Showing Timetable and Reminders inside Work was showing the wrong module's
furniture: they are personal, they do nothing for shared work, and their
presence made Work read as one page among five rather than as a place of its
own.
"""
import pytest

from tests.test_work_mode import _user, owner  # noqa: F401

PERSONAL_ONLY = ('href="/timetable"', 'href="/calendar"', 'href="/reminders"')
WORK_ONLY = ('href="/work/communities"', 'href="/work/tasks"')


@pytest.mark.parametrize("path", ["/dashboard", "/timetable", "/calendar", "/tasks", "/reminders"])
def test_personal_pages_show_the_personal_nav(owner, path):
    page = owner.get(path).text
    for link in PERSONAL_ONLY:
        assert link in page, f"{path} should offer {link}"
    for link in WORK_ONLY:
        assert link not in page, f"{path} should not offer {link}"


@pytest.mark.parametrize("path", ["/work", "/work/communities", "/work/tasks"])
def test_work_pages_show_the_work_nav(owner, path):
    page = owner.get(path).text
    for link in WORK_ONLY:
        assert link in page, f"{path} should offer {link}"
    for link in PERSONAL_ONLY:
        assert link not in page, f"{path} should not offer the personal {link}"


def test_the_switch_is_in_the_top_bar_on_every_page(owner):
    """Asked for there specifically, and it has to be on both sides or it is a
    one-way door."""
    for path in ("/dashboard", "/work", "/work/tasks", "/timetable"):
        page = owner.get(path).text
        assert 'class="mode-switch"' in page, path
        assert 'href="/dashboard"' in page and 'href="/work"' in page, path


def test_the_switch_marks_which_module_you_are_in(owner):
    personal = owner.get("/dashboard").text
    work = owner.get("/work").text

    # The half you are in is filled and marked as current; a switcher that
    # looked the same in both places would be decoration.
    assert personal.count("mode-opt is-on") == 1
    assert work.count("mode-opt is-on") == 1
    assert personal != work


def test_a_task_page_counts_as_work(owner):
    """Reached from a notification rather than from the nav, so the mode has to
    come from the path rather than from anything the reader clicked."""
    page = owner.get("/work/task/does-not-matter").text
    assert 'href="/work/communities"' in page
    assert 'href="/timetable"' not in page


def test_profile_stays_reachable_from_both(owner):
    for path in ("/dashboard", "/work"):
        assert 'href="/profile"' in owner.get(path).text, path


# --------------------------------------------------------------------------
# The three Work pages carry their own content and nobody else's
# --------------------------------------------------------------------------
def test_the_overview_carries_the_numbers_and_the_inbox(owner):
    page = owner.get("/work").text
    assert 'id="wk-n-active"' in page and 'id="wk-inbox-card"' in page
    # And not the lists that now live elsewhere -- two pages painting the same
    # container is how they drift apart.
    assert 'id="wk-communities"' not in page
    assert 'id="wk-active"' not in page


def test_the_communities_page_carries_communities(owner):
    page = owner.get("/work/communities").text
    assert 'id="wk-communities"' in page
    assert 'id="wk-new-community"' in page
    assert 'id="wk-n-active"' not in page


def test_the_tasks_page_carries_both_directions_of_work(owner):
    """What I am carrying and what I handed out, together: they are the same
    question asked twice, and splitting them would mean checking two places to
    know where a task stands."""
    page = owner.get("/work/tasks").text
    assert 'id="wk-active"' in page
    assert 'id="wk-created"' in page
    assert 'id="wk-created-sort"' in page


def test_every_work_page_still_gates_on_the_profile(owner):
    """The department gate is what makes Work usable at all; splitting the page
    must not have left a way in around it."""
    for path in ("/work", "/work/communities", "/work/tasks"):
        assert 'id="wk-profile-modal"' in owner.get(path).text, path


def test_the_new_pages_are_not_served_to_a_stranger(client):
    for path in ("/work/communities", "/work/tasks"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/login"


# --------------------------------------------------------------------------
# The switch is handed over between the two bars, never dropped
# --------------------------------------------------------------------------
def test_the_top_bar_switch_disappears_exactly_where_the_tab_bar_appears():
    """The invariant worth protecting: there must be no width at which neither
    bar offers a way between the modules.

    Checked in the stylesheets rather than in a browser because it is a
    statement about breakpoints, and the failure it guards against is somebody
    moving one number and not the other -- which no single-width screenshot
    would catch.
    """
    import re
    from pathlib import Path

    css = Path("app/static/css")
    style = (css / "style.css").read_text(encoding="utf-8")
    mobile = (css / "mobile.css").read_text(encoding="utf-8")

    def breakpoint_hiding(text: str, selector: str, value: str) -> int | None:
        """The max-width of the media query that sets `selector` to `value`."""
        for match in re.finditer(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{", text):
            start = match.end()
            depth, i = 1, start
            while i < len(text) and depth:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[start:i]
            if re.search(rf"{re.escape(selector)}\s*\{{[^}}]*display:\s*{value}", block):
                return int(match.group(1))
        return None

    tab_bar_appears = breakpoint_hiding(style + mobile, ".bottom-nav", "flex")
    switch_hides = breakpoint_hiding(mobile, ".mode-switch", "none")

    assert tab_bar_appears is not None, "could not find where .bottom-nav appears"
    assert switch_hides is not None, "could not find where .mode-switch hides"
    assert switch_hides == tab_bar_appears, (
        f"the top-bar switch hides at {switch_hides}px but the tab bar only "
        f"appears at {tab_bar_appears}px -- between those widths nothing offers "
        "a way between Personal and Work"
    )


def test_the_tab_bar_carries_the_switch_in_both_directions(owner):
    """What makes hiding the top-bar switch on mobile safe."""
    assert 'href="/work"' in owner.get("/dashboard").text
    work = owner.get("/work").text
    assert 'href="/dashboard"' in work
