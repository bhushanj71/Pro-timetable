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
    assert len(polygons) >= 10, "one stop per corner in the motion"
    for i, poly in enumerate(polygons):
        assert len(poly.split(",")) == 12, f"stop {i} has the wrong point count"


def test_the_staircase_moves_at_one_speed():
    """The stops have to be the moments a column starts or stops moving. Each
    column crosses over 60% of the run beginning 10% after the one before, so
    the corners are at 0/10/20/30/40 and 60/70/80/90/100. Sampling anywhere
    else cuts those corners and the columns visibly change speed partway.
    """
    block = re.search(r"@keyframes stairReveal\s*\{(.*?)\n\}", MOBILE, re.S).group(1)
    stops = [int(m) for m in re.findall(r"^\s*(\d+)%\s*\{", block, re.M)]
    assert stops == [0, 10, 20, 30, 40, 60, 70, 80, 90, 100]

    # And nothing may curve the run, or the stagger baked into those stops is
    # warped back out again.
    rule = MOBILE.split("::view-transition-new(root)")[1][:260]
    assert "stairReveal" in rule and "linear" in rule
    assert "cubic-bezier" not in rule


def test_the_transitions_are_given_time_to_be_seen():
    """Every one of these was too quick to read as motion. Guarded as floors
    rather than exact values so they can still be tuned, but not back down."""
    stair = int(re.search(r"animation: stairReveal (\d+)ms", MOBILE).group(1))
    glow = int(re.search(r"animation: tapGlowFade (\d+)ms", STYLE).group(1))
    pill = int(re.search(r"transition: translate (\d+)ms", STYLE).group(1))
    assert stair >= 800, "a five-column wipe needs longer than half a second"
    assert glow >= 1200, "a full turn of the border needs to be seen as one"
    assert pill >= 560


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
LANDING = (CSS / "landing.css").read_text(encoding="utf-8")


def _supports_block(css: str, feature: str) -> str:
    """The body of an @supports block, brace-matched so nested rules come with
    it -- the whole point here is what is *inside* the guard."""
    start = css.find("@supports (" + feature + ")")
    assert start != -1, f"@supports ({feature}) should guard this"
    open_at = css.index("{", start)
    depth, i = 0, open_at
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_at + 1:i]
        i += 1
    raise AssertionError("unbalanced @supports block")


def test_the_scroll_reveal_cannot_hide_the_page_it_decorates():
    """It starts at opacity 0. If the rule applied where the timeline is not
    supported -- Firefox, older Safari -- the entire feature grid would be
    permanently invisible, so the guard is load-bearing, not politeness."""
    guarded = _supports_block(LANDING, "animation-timeline: view()")
    assert "opacity: 0" in guarded, "the reveal lives inside the guard"
    assert "opacity: 0" not in LANDING.replace(guarded, ""), "and nowhere outside it"
    assert ".lp-feature" in guarded and ".lp-step" in guarded


def test_the_scroll_reveal_stops_when_motion_is_not_wanted():
    guarded = _supports_block(LANDING, "animation-timeline: view()")
    assert "prefers-reduced-motion: no-preference" in guarded


def test_the_reveal_does_not_steal_the_hover():
    """A filled animation holds its last value for good. On transform it would
    win every subsequent hover, and these cards lift on hover -- so the reveal
    moves `translate` and leaves transform alone."""
    guarded = _supports_block(LANDING, "animation-timeline: view()")
    reveal = guarded[guarded.index("@keyframes lpReveal"):]
    assert "translate:" in reveal
    assert "transform:" not in reveal
    assert "transform: translateY(-4px)" in LANDING, "the hover lift is still there"


def test_nothing_fixed_lives_inside_a_revealed_card():
    """The reveal leaves a `translate` on the card for good, and that makes it
    the containing block for any position:fixed descendant -- the same trap
    that put every admin dialog below the fold."""
    landing_html = Path("app/templates/landing.html").read_text(encoding="utf-8")
    for chunk in re.findall(r'<div class="lp-(?:feature|step)".*?</div>\s*</div>', landing_html, re.S):
        assert "modal-backdrop" not in chunk


def test_the_field_behind_the_app_moves_with_the_document():
    """The parallax rides the document's own scroller: the field is fixed to
    the viewport, not to any panel inside it."""
    guarded = _supports_block(GLASS, "animation-timeline: scroll()")
    assert "body.app-shell::before" in guarded
    assert "animation-timeline: scroll(root block)" in guarded
    assert "prefers-reduced-motion: no-preference" in guarded


def test_the_two_hero_arcs_move_at_different_rates():
    """Together they read as one object sliding. Apart, as two at different
    depths -- which is the entire point of a parallax."""
    guarded = _supports_block(STYLE, "animation-timeline: view()")
    near = re.search(r"@keyframes heroArcDrift\s*\{.*?to\s*\{\s*translate:\s*0\s+(\d+)px", guarded, re.S)
    far = re.search(r"@keyframes heroArcDriftFar\s*\{.*?to\s*\{\s*translate:\s*0\s+(\d+)px", guarded, re.S)
    assert near and far, "both arcs should have their own keyframes"
    assert int(near.group(1)) != int(far.group(1))


def test_the_glow_fades_inward_rather_than_ending_at_a_line():
    """One gradient per edge, added together, so the light is solid where it
    meets the screen and thins to nothing on the way in.

    The mask is the only thing keeping this from being a sheet of colour over
    the entire page, so a layer that fails to parse is not a lost effect -- it
    is an unreadable application. And the falloff has to be in the mask: a
    band still has an inner edge however heavily it is blurred.
    """
    rule = _rule(STYLE, ".tap-glow")
    for direction in ("to bottom", "to top", "to right", "to left"):
        assert rule.count(f"linear-gradient({direction},") == 2, \
            f"{direction} edge, once for mask and once for -webkit-mask"
    assert "mask-composite: add" in rule
    assert "-webkit-mask-composite: source-over" in rule
    assert "padding:" not in rule, "the band is gone; the gradient is the falloff"

    # Curved, not a straight ramp: most of the brightness in the first third.
    stops = re.findall(r"rgba\(0,0,0,([\d.]+)\) (\d+)%", rule)
    assert ("0.55", "30") in stops and ("0.16", "62") in stops


def test_the_blur_is_not_doing_the_falloffs_work():
    """It softens the colour steps in the wheel. A heavy blur across the whole
    viewport is the expensive part of this, and once the mask owns the shape
    there is nothing for a big one to add."""
    rule = _rule(STYLE, ".tap-glow")
    blur = int(re.search(r"filter: blur\((\d+)px\)", rule).group(1))
    assert blur <= 8


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


def test_a_glow_cannot_be_left_on_the_page():
    """animationend never arrives in a backgrounded tab, and a lit edge
    stranded over the page is worse than no glow at all."""
    assert "SAFETY_MS" in TAP_JS
    assert "setTimeout" in TAP_JS
    assert '"pagehide", clear' in TAP_JS


def test_the_glow_frames_the_page_and_not_the_control():
    """Skiper86 is a border around the screen. Hugging each control instead
    meant measuring a rectangle, following it, and giving up when the page
    scrolled -- none of which a viewport frame has to do, which is why there is
    no geometry left in this file."""
    rule = _rule(STYLE, ".tap-glow")
    assert "inset: 0" in rule
    assert "getBoundingClientRect" not in TAP_JS, "no geometry to keep"
    assert '"scroll"' not in TAP_JS, "and nothing to invalidate on scroll"


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
