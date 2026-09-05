"""Moving between pages.

The same movement the theme toggle makes: a circle opening out, with a soft
edge. It was a double staircase before -- five hard-edged panels arriving on
five different clocks, precise, and mechanical in a way that read as machinery
every single time you moved between two pages.

This is one shape with no edge to speak of: an overlay in the page's own
colour with a hole in it that grows, so the page shows through the hole. The
softness is a gradient stop rather than a blur filter, which costs nothing and
cannot band across a large surface.

The care here is not in how it looks. The overlay rests in the covering
position, so every failure mode is a blank screen rather than a missing
flourish, and three separate things have to hold: the opening must not need
JavaScript, the circle must clear the corners, and something must remove the
element regardless.
"""
import re
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fresh_db():
    """These read files; they open no session."""
    yield


STYLE = Path("app/static/css/style.css").read_text(encoding="utf-8")
THEME = Path("app/static/css/theme.css").read_text(encoding="utf-8")
APP_JS = Path("app/static/js/app.js").read_text(encoding="utf-8")
BASE = Path("app/templates/base.html").read_text(encoding="utf-8")


def _css(t):
    return re.sub(r"/\*.*?\*/", "", t, flags=re.S)


def _js(t):
    body = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))


def _jinja(t):
    return re.sub(r"\{#.*?#\}", "", t, flags=re.S)


def _rule(css, selector):
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", _css(css), re.S)
    assert m, f"{selector} should be a rule"
    return m.group(1)


# ---------------------------------------------------------------------------
# The page must always end up visible
# ---------------------------------------------------------------------------
def test_the_opening_does_not_need_javascript():
    """The overlay rests covering the page, so a page that waited for a script
    to uncover it would show nothing at all when the script failed."""
    html = _jinja(BASE)
    assert 'class="page-wipe page-wipe-open"' in html, "server-rendered, not built by JS"
    assert html.index("<body") < html.index("page-wipe") < html.index('class="layout"'), \
        "painted before the content it covers"


def test_the_circle_clears_the_corners():
    """It is measured against the box, so a circle that only reaches the edge
    leaves the four corners covered for good."""
    frames = re.search(r"@keyframes wipeOpen\s*\{(.*?)\}\s*\n", _css(STYLE), re.S).group(1)
    end = re.search(r"to\s*\{\s*--wipe-r:\s*(\d+)%", frames)
    assert end and int(end.group(1)) > 141, \
        "a square's diagonal is about 141% of its side; less leaves corners covered"


def test_there_is_a_way_out_even_if_the_animation_never_runs():
    """The one failure CSS cannot cover for, and the worst outcome available
    here: a blank screen rather than a missing flourish."""
    assert "setTimeout(() => wipe.remove()" in _js(APP_JS)


def test_the_overlay_never_swallows_a_click():
    assert "pointer-events: none" in _rule(STYLE, ".page-wipe")


def test_the_radius_is_a_registered_property():
    """Unregistered it is a string swap: the hole appears at full size instead
    of growing, which is not an animation at all."""
    block = re.search(r"@property --wipe-r\s*\{(.*?)\}", _css(STYLE), re.S)
    assert block, "the radius has to be registered"
    assert "length-percentage" in block.group(1)


def test_reduced_motion_gets_nothing_between_pages():
    reduced = re.findall(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}",
                         _css(STYLE), re.S)
    assert any(".page-wipe" in b and "display: none" in b for b in reduced)


def test_the_overlay_holds_position_through_its_own_timing():
    """forwards alone leaves it at rest before it starts, and at rest means
    uncovered -- so the page would flash through before the circle opened."""
    assert "both" in _rule(STYLE, ".page-wipe-open")


# ---------------------------------------------------------------------------
# It has to be the same movement as the theme toggle
# ---------------------------------------------------------------------------
def test_it_is_the_same_movement_as_the_theme_toggle():
    """That is the whole brief. Two circles opening at different speeds are
    two effects rather than one idea."""
    page = re.search(r"\.page-wipe-open\s*\{\s*animation:\s*wipeOpen\s+(\d+)ms\s+var\((--[\w-]+)\)",
                     _css(STYLE))
    theme = re.search(r"animation:\s*themeReveal\s+(\d+)ms\s+var\((--[\w-]+)\)", _css(THEME))
    assert page and theme, "both have to state their timing"
    assert page.group(1) == theme.group(1), \
        f"the page opens in {page.group(1)}ms and the theme in {theme.group(1)}ms"
    assert page.group(2) == theme.group(2), "and they have to share a curve"


def test_the_edge_is_soft():
    """A hard stop is a circle drawn over the page. The feather is what makes
    it read as light instead."""
    rule = _rule(STYLE, ".page-wipe")
    assert "radial-gradient" in rule
    assert re.search(r"calc\(var\(--wipe-r\) \+ \d+%\)", rule), \
        "the opaque stop has to sit outside the transparent one"


# ---------------------------------------------------------------------------
# Leaving, and where the circle comes from
# ---------------------------------------------------------------------------
def test_a_departing_page_closes_the_same_circle():
    js = _js(APP_JS)
    assert '"page-wipe page-wipe-close"' in js
    body = js.split("function showRouteVeil")[1][:900]
    assert "loaderMarkup(" not in body, "the circle is the signal; a spinner as well is two"


def test_the_circle_closes_from_where_the_press_landed():
    """What makes the movement feel caused rather than merely scheduled."""
    js = _js(APP_JS)
    assert "pointerdown" in js
    assert "--wipe-x" in js and "--wipe-y" in js
    listener = js.split('addEventListener("pointerdown"')[1][:220]
    assert "true)" in listener, \
        "capture phase, or a control that stops propagation hides the press"


def test_the_next_page_opens_from_the_same_spot():
    js = _js(APP_JS)
    assert 'sessionStorage.setItem("ps-wipe"' in js
    assert 'sessionStorage.getItem("ps-wipe")' in js


def test_the_stored_point_is_used_once():
    """Left behind, it opens the next page from wherever an unrelated link was
    pressed several navigations ago."""
    assert 'sessionStorage.removeItem("ps-wipe")' in _js(APP_JS)


def test_storage_being_unavailable_loses_the_origin_and_not_the_page():
    """Private mode with storage disabled throws on access. The circle can
    fall back to the middle; it cannot fall back to not opening."""
    js = _js(APP_JS)
    for call in ('sessionStorage.setItem("ps-wipe"', 'sessionStorage.getItem("ps-wipe")'):
        assert "try {" in js[:js.index(call)][-240:], f"{call} has to sit inside a try"


def test_the_departure_keeps_the_identity_the_rest_of_the_app_looks_for():
    """busy.js suppresses its own pill when this exists, and hideRouteVeil
    removes it by id. Renaming the element quietly breaks both."""
    assert 'veil.id = "route-veil"' in _js(APP_JS)
    busy = _js(Path("app/static/js/busy.js").read_text(encoding="utf-8"))
    assert 'getElementById("route-veil")' in busy


def test_the_staircase_is_gone():
    """Replaced, not layered over. Two page transitions is one too many, and
    the leftovers of the first one are what the second one trips on."""
    for name, text in (("style.css", STYLE), ("app.js", APP_JS), ("base.html", BASE)):
        assert "stairs" not in text, f"{name} still carries the staircase"
