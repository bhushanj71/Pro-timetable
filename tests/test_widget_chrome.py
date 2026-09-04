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


def _hex(css: str, token: str, *, occurrence: int = 0) -> str:
    found = re.findall(rf"{re.escape(token)}:\s*(#[0-9a-fA-F]{{6}})", css)
    assert found, f"{token} should be a plain hex so it can be compared"
    return found[occurrence]


def _luminance(colour: str) -> float:
    """WCAG relative luminance, for comparing two swatches."""
    parts = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def test_the_frame_recedes_in_both_themes():
    """A band lighter than the panel it holds reads as a highlight, not a
    mount. Asserted as the relationship rather than as two hex values: the
    palette is allowed to change, this is not. Dark is the one that catches
    people out -- there --surface-alt is the *lighter* step, so a frame that
    reaches for it comes out in front of the panel.
    """
    theme = (CSS / "theme.css").read_text(encoding="utf-8")
    for name, css in (("light", STYLE), ("dark", theme)):
        frame = _luminance(_hex(css, "--frame-fill"))
        surface = _luminance(_hex(css, "--surface"))
        assert frame < surface, f"{name}: the frame must sit behind its panel"
    assert len(re.findall(r"--frame-fill:", theme)) == 2, "dark and system-dark both"


def test_the_page_is_neither_white_nor_cream():
    """It was two points off white and warm with it, so a white card had
    nothing to sit against and the screen read as cream. The page has to stay
    clear of both ends, and --text-muted sitting directly on it has to keep
    its AA contrast -- which is what stops this being taken further down.
    """
    page = _hex(STYLE, "--bg")
    r, g, b = (int(page[i:i + 2], 16) for i in (1, 3, 5))
    assert r <= 246, "still too close to white to read as a page"
    assert r - b <= 6, "too warm; that is what made it look cream"

    muted = _luminance(_hex(STYLE, "--text-muted"))
    bg = _luminance(page)
    hi, lo = max(muted, bg), min(muted, bg)
    assert (hi + 0.05) / (lo + 0.05) >= 4.5, "muted text on the page must still meet AA"


MOBILE = (CSS / "mobile.css").read_text(encoding="utf-8")


def test_the_page_transition_is_a_staircase():
    """Five columns, staggered. clip-path can only interpolate between
    polygons with the same number of points, so this is one twelve-point shape
    whose five top edges rise at different times -- a stop with the wrong
    point count would silently snap instead of animating.
    """
    block = re.search(r"@keyframes stairReveal\s*\{(.*?)\n\}", MOBILE, re.S)
    assert block, "the staircase keyframes should still be here"
    polygons = re.findall(r"clip-path:\s*polygon\((.*?)\)", block.group(1), re.S)
    assert len(polygons) == 5, "five stops"
    for i, poly in enumerate(polygons):
        assert len(poly.split(",")) == 12, f"stop {i} has the wrong point count"


def test_the_transition_is_the_browsers_and_not_an_overlay_of_ours():
    """An element of ours covering the viewport during a navigation is a blank
    screen waiting to happen. The browser owns these snapshots and removes them
    whatever becomes of the page."""
    assert "@view-transition { navigation: auto; }" in MOBILE
    assert "::view-transition-new(root)" in MOBILE
    assert "stairReveal" in MOBILE.split("::view-transition-new(root)")[1][:200]


def test_the_page_keeps_its_own_entrance_for_browsers_without_the_transition():
    """Firefox has no cross-document view transitions. If the browser skips
    the transition, vt-running is never set and pageIn still runs -- so the
    fallback must not have been deleted along the way."""
    assert "pageIn" in _rule(MOBILE, ".content, .landing, .auth-wrap")


def test_standing_the_entrance_down_does_not_reintroduce_a_transform():
    """The suppression has to be `animation: none`. Anything that leaves a
    transform on .content makes it the containing block for the dialogs inside
    it, which is the bug fixed above."""
    rule = _rule(MOBILE, ":root.vt-running .content,\n:root.vt-running .landing,\n:root.vt-running .auth-wrap")
    assert "animation: none" in rule
    assert "transform" not in rule


def test_the_sweep_stops_when_motion_is_not_wanted():
    reduced = MOBILE.split("@media (prefers-reduced-motion: reduce)")[1][:400]
    assert "::view-transition-new(root) { animation: none; }" in reduced


TAP_JS = Path("app/static/js/tap-glow.js").read_text(encoding="utf-8")


def test_the_tap_glow_is_a_ring_and_not_a_fill():
    """Two mask layers subtracted from each other leave the padding ring. With
    one, the gradient is a filled rectangle sitting over the label of whatever
    was just pressed."""
    rule = _rule(STYLE, ".tap-glow")
    assert rule.count("linear-gradient(#000 0 0)") == 4, "two layers, twice for -webkit-"
    assert "mask-composite: exclude" in rule
    assert "-webkit-mask-composite: xor" in rule


def test_the_tap_glow_cannot_intercept_the_press_it_is_reporting():
    rule = _rule(STYLE, ".tap-glow")
    assert "pointer-events: none" in rule
    assert "position: fixed" in rule
    # Above a dialog (200) so a button inside one still shows it, below the
    # toasts (300) and the route veil (400), which are messages not decoration.
    z = int(re.search(r"z-index:\s*(\d+)", rule).group(1))
    assert 200 < z < 300


def test_the_turn_is_even():
    """The fade and the turn want opposite curves. Sharing one ease-out put
    the ring at 297 degrees a quarter of the way in -- a flick, then a crawl --
    so they are two animations, and the spin must stay linear."""
    rule = _rule(STYLE, ".tap-glow")
    assert "tapGlowFade" in rule and "tapGlowSpin" in rule
    spin = re.search(r"tapGlowSpin\s+\d+ms\s+(\w+)", rule)
    assert spin and spin.group(1) == "linear"


def test_the_angle_is_a_registered_property():
    """conic-gradient(from var(--tap-angle)) only animates if the property is
    registered as an angle. Unregistered it is a string swap and the ring does
    not turn at all."""
    block = re.search(r"@property --tap-angle\s*\{(.*?)\}", STYLE, re.S)
    assert block, "the angle has to be registered"
    assert 'syntax: "<angle>"' in block.group(1)


def test_the_glow_wears_the_applications_own_colours():
    """The source is an Apple spectrum. A rainbow is the one thing in this
    palette that would read as belonging to a different program."""
    rule = _rule(STYLE, ".tap-glow")
    gradient = re.search(r"background:\s*conic-gradient\((.*?)\);", rule, re.S).group(1)
    assert "#" not in gradient, "no hard-coded hues; the theme owns these"
    for token in ("--primary", "--warning", "--success", "--cat-lab"):
        assert token in gradient


def test_every_control_gets_it_without_being_wired_up():
    """One delegated listener, in the capture phase so components that stop
    propagation on their own buttons do not silently opt out."""
    assert "document.addEventListener(\"click\"" in TAP_JS
    assert "true);" in TAP_JS.split("document.addEventListener(\"click\"")[1][:400]
    for target in ('"button"', '"label"', '".nav-link"', '".bn-item"', '".icon-btn"'):
        assert target in TAP_JS


def test_a_refused_press_is_not_congratulated():
    assert "el.disabled" in TAP_JS
    assert 'aria-disabled' in TAP_JS


def test_a_ring_cannot_be_left_on_the_page():
    """animationend never arrives in a backgrounded tab, and a ring stranded
    over the page is worse than no ring at all."""
    assert "SAFETY_MS" in TAP_JS
    assert "setTimeout" in TAP_JS
    # Anchored to viewport coordinates, so it stops meaning anything once the
    # page moves underneath it.
    for event in ("scroll", "resize", "pagehide"):
        assert f'"{event}", clear' in TAP_JS or f'"{event}"' in TAP_JS


def test_the_glow_is_loaded_everywhere():
    assert "tap-glow.js" in Path("app/templates/base.html").read_text(encoding="utf-8")


def test_the_ambient_field_is_damped_for_dark():
    """It was only ever set in light, so the same four colour blooms sat
    behind the dark theme at full strength and lifted a near-black palette
    into warm brown."""
    light = re.search(r"--amb-1: rgba\([^)]*?([\d.]+)\)", GLASS)
    darks = re.findall(r"--amb-1: rgba\([^)]*?([\d.]+)\)", GLASS)
    assert len(darks) == 3, "light, dark, and system-dark"
    assert all(float(d) < float(light.group(1)) for d in darks[1:]), \
        "dark needs less of the field than light does"


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
