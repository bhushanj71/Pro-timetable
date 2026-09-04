"""The widget frame and the travelling nav indicator.

Neither can be proved by a test the way a route can -- what they look like is
the point, and that was checked in a browser. What is worth pinning here is
the part that silently rots: the markup and the CSS contract the JavaScript
depends on, and the fallbacks that have to hold when the JavaScript does not
run at all.
"""
import re
from pathlib import Path

import pytest

from tests.test_work_mode import _user, owner  # noqa: F401

CSS = Path("app/static/css")
STYLE = (CSS / "style.css").read_text(encoding="utf-8")
GLASS = (CSS / "glass.css").read_text(encoding="utf-8")
NAV_PILL_JS = Path("app/static/js/nav-pill.js").read_text(encoding="utf-8")

# Every widget that was given the frame, with the padding it had before, so a
# later edit cannot quietly change how much room the content gets.
FRAMED = {
    ".card": (6, "14px 16px", 20, 22),
    ".qa-card": (4, "12px 14px", 16, 18),
    ".stat-card": (5, "13px", 18, 18),
    ".modal-card": (8, "18px", 26, 26),
}


def _rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert match, f"{selector} should still be a rule"
    return match.group(1)


@pytest.mark.parametrize("selector", sorted(FRAMED))
def test_the_frame_does_not_change_the_content_box(selector):
    """The frame is paint, not layout: border plus padding has to come to what
    the padding alone used to be, or every card on every page shifts."""
    frame, padding, was_v, was_h = FRAMED[selector]
    body = _rule(STYLE, selector)
    assert f"--frame: {frame}px" in body, selector
    assert f"padding: {padding}" in body, selector

    parts = padding.split()
    vertical = int(parts[0].removesuffix("px"))
    horizontal = int((parts[1] if len(parts) > 1 else parts[0]).removesuffix("px"))
    assert vertical + frame == was_v, f"{selector} vertical inset moved"
    assert horizontal + frame == was_h, f"{selector} horizontal inset moved"


@pytest.mark.parametrize("selector", sorted(FRAMED))
def test_the_panel_does_not_flood_its_own_frame(selector):
    """background-clip is the whole mechanism. Without it the surface paints
    under the border and there is no frame at all -- and a `background`
    shorthand anywhere later silently resets it."""
    assert "background-clip: padding-box" in _rule(STYLE, selector), selector


@pytest.mark.parametrize("selector", [".card", ".modal-card"])
def test_the_frame_survives_the_glass_material(selector):
    """glass.css restates background and border for these two, which would
    otherwise undo both the clip and the band."""
    body = _rule(GLASS, selector)
    assert "background-clip: padding-box" in body, selector
    assert "var(--frame) solid var(--glass-frame)" in body, selector


def test_the_frame_recedes_in_both_themes():
    """A band that is lighter than its panel reads as a highlight, not a
    mount. Light steps down from #ffffff; dark has to step down from #201b18
    rather than reach for the lighter --surface-alt."""
    assert "--frame-fill: #f4eae3" in STYLE
    theme = (CSS / "theme.css").read_text(encoding="utf-8")
    assert theme.count("--frame-fill: #17120f") == 2, "dark and system-dark both"


@pytest.mark.parametrize("path", ["/dashboard", "/work", "/timetable"])
def test_both_navs_carry_a_pill(owner, path):
    page = owner.get(path).text
    assert page.count('class="nav-pill"') == 2, f"{path}: sidebar and tab bar"
    assert "/static/js/nav-pill.js" in page, path


def test_the_pill_is_hidden_from_assistive_technology(owner):
    """It is a moving background for a link that already says where it goes."""
    page = owner.get("/dashboard").text
    assert page.count('<span class="nav-pill" aria-hidden="true"></span>') == 2


def test_the_old_indicator_survives_as_a_fallback():
    """If the script does not run there is still something marking the current
    tab, so the bar is only suppressed once the pill is actually live."""
    mobile = (CSS / "mobile.css").read_text(encoding="utf-8")
    assert ".bn-item.active::before {" in mobile
    assert ".bottom-nav.has-pill .bn-item.active::before { display: none; }" in mobile
    assert ".sidebar.has-pill .nav-link.active" in STYLE


def test_the_arrival_pulse_cannot_hide_a_label():
    """A blur-and-fade on the label was tried and rejected: anything that stops
    the animation finishing leaves a primary nav item unreadable. It rides the
    icon, and it animates `scale` so the active states' own transforms are not
    overwritten."""
    assert "filter: blur" not in _rule(STYLE, "@keyframes navArrive")
    assert "opacity" not in _rule(STYLE, "@keyframes navArrive")
    assert ".bn-item.is-arriving .bn-ico" in STYLE
    assert ".nav-link.is-arriving .nav-ico" in STYLE


def test_the_calendar_switcher_marks_the_current_view(owner):
    """The stylesheet always had a rule for the selected view and nothing ever
    set the class, so the switcher showed nothing at all."""
    page = owner.get("/calendar").text
    assert 'class="calendar-view-switch"' in page
    assert page.count('class="nav-pill"') == 3, "sidebar, tab bar and the switcher"
    calendar_js = Path("app/static/js/calendar.js").read_text(encoding="utf-8")
    assert "function syncViewButtons" in calendar_js
    assert "syncViewButtons();" in calendar_js.split("async function renderCalendar")[1]


def test_a_page_with_no_matching_tab_keeps_its_plain_active_state(owner):
    """Profile is not one of the five tabs. The pill is removed rather than
    parked somewhere arbitrary."""
    assert "pill.parentNode.removeChild(pill)" in NAV_PILL_JS
    page = owner.get("/profile").text
    assert 'class="nav-pill"' in page  # served; the script removes it client-side


def test_a_re_measure_does_not_kill_a_travel_in_flight():
    """Fonts finish loading a beat after the page does, squarely inside the
    travel. Cancelling the transition to correct the geometry would stop the
    pill dead halfway, so an in-flight pill is retargeted instead."""
    assert "if (travelling) { settle(); return; }" in NAV_PILL_JS
    assert "travelling = true;" in NAV_PILL_JS


def test_the_travel_is_skipped_when_motion_is_not_wanted():
    assert "!reducedMotion()" in NAV_PILL_JS
    assert "prefers-reduced-motion: reduce" in STYLE


def test_the_page_transition_does_not_capture_fixed_dialogs():
    """`both` keeps the last keyframe applied for ever, and a filled
    `transform: none` computes to the identity matrix rather than to none.
    Any computed transform makes the element the containing block for its
    position:fixed descendants, which had .content capturing every modal
    backdrop inside it: `inset: 0` meant the document, not the viewport, so
    dialogs centred on the middle of the page. On the admin panel that put all
    five of them below the fold.

    `backwards` still covers the state before the animation starts, which is
    all the fill was needed for.
    """
    mobile = (CSS / "mobile.css").read_text(encoding="utf-8")
    rule = _rule(mobile, ".content, .landing, .auth-wrap")
    assert "pageIn" in rule
    assert "backwards" in rule, "a page transition must not outlive itself as a transform"
    assert " both;" not in rule


def test_the_dialogs_that_were_captured_are_still_inside_the_content_block():
    """The fix is the fill mode, not moving the markup. These dialogs live
    inside the content block, so if a new transform ever lands on an ancestor
    the rule above starts mattering again -- keep them found together.

    They are split across two templates now: the college and department
    dialogs stayed on the admin overview, and the user form and password reset
    followed user management onto its own page.
    """
    admin = Path("app/templates/admin.html").read_text(encoding="utf-8")
    members = Path("app/templates/admin_members.html").read_text(encoding="utf-8")
    assert admin.count('class="modal-backdrop hidden"') == 2
    assert members.count('class="modal-backdrop hidden"') == 2
    for name, page in (("admin", admin), ("members", members)):
        body = page.split("{% block content %}", 1)[1]
        assert "modal-backdrop" in body, f"{name}: dialogs must stay in the content block"
