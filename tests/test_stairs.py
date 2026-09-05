"""The double staircase between pages.

Two staircases: one coming down from the top, one going up from the bottom.
The screen is cut into columns, each with a panel at the top and a panel at
the bottom, and they leave towards their own edge. The stagger across the
columns is the entire effect -- without it the halves part in a straight line
and it is a curtain.

The thing worth being careful about is not how it looks. The panels' resting
position is *covering the page*, so every failure mode here is a blank screen
rather than a missing flourish. Three separate things have to hold:

  * the reveal must not need JavaScript, because a page whose scripts never
    arrive still has to uncover itself;
  * the panels must end fully off the screen, not nearly;
  * and there is a timeout that removes the whole thing regardless, for the
    one case CSS cannot cover for -- animations not running at all.
"""
import re
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fresh_db():
    """These read files; they open no session."""
    yield


STYLE = Path("app/static/css/style.css").read_text(encoding="utf-8")
APP_JS = Path("app/static/js/app.js").read_text(encoding="utf-8")
BASE = Path("app/templates/base.html").read_text(encoding="utf-8")


def _css(t: str) -> str:
    return re.sub(r"/\*.*?\*/", "", t, flags=re.S)


def _js(t: str) -> str:
    body = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))


def _jinja(t: str) -> str:
    return re.sub(r"\{#.*?#\}", "", t, flags=re.S)


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", _css(css), re.S)
    assert m, f"{selector} should be a rule"
    return m.group(1)


# ---------------------------------------------------------------------------
# The page must always end up visible
# ---------------------------------------------------------------------------
def test_the_reveal_does_not_need_javascript():
    """The panels rest in the covering position, so a page that waits for a
    script to uncover it is a page that shows nothing when the script fails."""
    html = _jinja(BASE)
    assert 'class="stairs stairs-reveal"' in html, "rendered by the server, not built by JS"
    body_at = html.index("<body")
    stairs_at = html.index('class="stairs stairs-reveal"')
    layout_at = html.index('class="layout"')
    assert body_at < stairs_at < layout_at, "painted before the content it covers"


def test_the_panels_end_completely_off_the_screen():
    """99% leaves a sliver of panel against the edge on a fractional viewport
    height, and a sliver of the wrong colour reads as a rendering fault."""
    css = _css(STYLE)
    for name in ("stairLeaveUp", "stairLeaveDown"):
        frames = re.search(r"@keyframes\s+" + name + r"\s*\{(.*?)\}\s*\n", css, re.S)
        assert frames, f"{name} should exist"
        assert "101%" in frames.group(1), f"{name} must clear the edge outright"


def test_there_is_a_way_out_even_if_the_animations_never_run():
    """The one failure CSS cannot cover for. A blank screen is the worst
    outcome available here, so it costs one timeout to make it impossible."""
    js = _js(APP_JS)
    assert '.stairs-reveal' in js
    assert "setTimeout" in js.split(".stairs-reveal")[0][-260:], \
        "the removal has to be on a timer, not on an event that may not fire"


def test_the_staircase_never_swallows_a_click():
    """It stays in the document after its one job, so it has to be inert for
    the rest of the page's life, not only while it is moving."""
    assert "pointer-events: none" in _rule(STYLE, ".stairs")


def test_reduced_motion_gets_no_staircase_at_all():
    """Half a second of moving panels between every page is precisely what
    this preference asks not to be given, and the page is ready either way."""
    reduced = re.findall(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}",
                         _css(STYLE), re.S)
    assert any(".stairs" in b and "display: none" in b for b in reduced), \
        "the whole thing goes, rather than being animated more gently"


# ---------------------------------------------------------------------------
# What makes it a staircase rather than a curtain
# ---------------------------------------------------------------------------
def test_the_columns_are_staggered():
    css = _css(STYLE)
    delays = re.findall(r"\.stairs i:nth-child\((\d)\)[^{]*\{\s*animation-delay:\s*calc\((\d)",
                        css)
    assert len(delays) >= 4, "every column needs its own delay"
    steps = [int(step) for _, step in delays]
    assert steps == sorted(steps) and len(set(steps)) == len(steps), \
        "the delays have to increase across the columns, or the edge is straight"


def test_every_column_has_a_delay():
    """A column without one snaps ahead of the rest and breaks the line."""
    css = _css(STYLE)
    cols = int(re.search(r"--stair-cols:\s*(\d+)", css).group(1))
    given = len(re.findall(r"\.stairs i:nth-child\(\d\)::before", css))
    assert given == cols, f"{cols} columns but {given} delays"


def test_the_two_halves_go_to_opposite_edges():
    """Both leaving the same way is one staircase, and the point is two."""
    css = _css(STYLE)
    up = re.search(r"@keyframes stairLeaveUp\s*\{(.*?)\}\s*\n", css, re.S).group(1)
    down = re.search(r"@keyframes stairLeaveDown\s*\{(.*?)\}\s*\n", css, re.S).group(1)
    assert "-101%" in up and "101%" in down and "-101%" not in down


def test_the_panels_hold_position_through_their_own_delay():
    """With forwards alone, a panel waiting its turn sits at rest -- which for
    the covering direction means uncovered, so the page flashes through before
    each column arrives."""
    assert "animation-fill-mode: both" in _rule(STYLE, ".stairs i::before,\n.stairs i::after")


def test_the_panels_overlap_their_column():
    """Column widths land on fractions of a pixel. Without the overlap the
    page shows through as hairlines between the panels and along the middle."""
    rule = _rule(STYLE, ".stairs i::before,\n.stairs i::after")
    assert "left: -1px" in rule and "right: -1px" in rule
    height = re.search(r"height:\s*([\d.]+)%", rule)
    assert height and float(height.group(1)) > 50, "past half, or the seam shows"


def test_the_movement_is_a_translate_and_not_a_transform():
    css = _css(STYLE)
    for name in ("stairLeaveUp", "stairLeaveDown", "stairArriveDown", "stairArriveUp"):
        frames = re.search(r"@keyframes\s+" + name + r"\s*\{(.*?)\}\s*\n", css, re.S).group(1)
        assert "translate:" in frames and "transform" not in frames


# ---------------------------------------------------------------------------
# Leaving is the same movement backwards
# ---------------------------------------------------------------------------
def test_a_departing_page_closes_the_same_staircase():
    js = _js(APP_JS)
    assert '"stairs stairs-cover"' in js
    assert "loaderMarkup(" not in js.split("function showRouteVeil")[1][:700], \
        "the staircase is the signal now; a spinner as well is two of them"


def test_the_departure_keeps_the_identity_the_rest_of_the_app_looks_for():
    """busy.js suppresses its own pill when this exists, and hideRouteVeil
    removes it by id. Renaming the element quietly breaks both."""
    js = _js(APP_JS)
    assert 'veil.id = "route-veil"' in js
    busy = _js(Path("app/static/js/busy.js").read_text(encoding="utf-8"))
    assert 'getElementById("route-veil")' in busy


def test_the_label_waits_for_the_panels_to_meet():
    """Read through a closing gap it is unreadable, and it is the only thing
    saying what the wait is for."""
    rule = _rule(STYLE, ".stairs-label")
    assert "animation-delay" in rule
    assert "--stair-ms" in rule and "--stair-step" in rule, \
        "derived from the movement, or the two drift apart"
