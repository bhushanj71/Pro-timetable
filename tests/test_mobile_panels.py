"""The two bars at the ends of a phone screen.

They are meant to be the same object twice -- a pane inset from the edges with
corners that follow the phone's own -- and they had drifted apart: one floated,
the other was bolted across the top and held there while the page slid under
it.

Three things are pinned here, because each is invisible to every other test and
each is something somebody actually saw and reported:

  * the top bar goes with the page rather than staying put,
  * it is shaped like the tab bar,
  * nothing from the page shows through the space the tab bar floats in.

None of it can be proved by rendering, so what is checked is the CSS contract:
the declarations that make it true, and the places it has to be switched off.
"""
import re
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fresh_db():
    """Overrides the suite-wide database reset, which these tests do not need.

    Every test here reads a stylesheet off disk. Inherited, the reset dropped
    and rebuilt the whole schema before and after each of them -- twenty-odd
    seconds for a file of text assertions, and often enough a teardown that
    raced its own existence check and errored on a table it had just been told
    was there. Nothing here opens a session, so there is nothing to isolate.
    """
    yield


CSS = Path("app/static/css")
MOBILE = (CSS / "mobile.css").read_text(encoding="utf-8")
GLASS = (CSS / "glass.css").read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Source with block comments removed.

    Every check below would otherwise be able to match the paragraph that
    explains it rather than the declaration that implements it. That has
    caught this suite out more than once.
    """
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _blocks(css: str, opener: str) -> list[str]:
    """Every block introduced by `opener`, brace-matched.

    A regex cannot do this: these blocks nest, so stopping at the first closing
    brace returns the first rule inside the block instead of the block.
    """
    out = []
    for m in re.finditer(re.escape(opener), css):
        i = css.index("{", m.end())
        depth, j = 1, i + 1
        while depth and j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        out.append(css[i + 1:j - 1])
    return out


def _rule(block: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", block, re.S)
    assert m, f"{selector} should be a rule here"
    return m.group(1)


def _phone(css: str) -> str:
    """Everything that applies to a 768px-wide screen."""
    return "\n".join(_blocks(_code(css), "@media (max-width: 768px)"))


# ---------------------------------------------------------------------------
# The top bar
# ---------------------------------------------------------------------------
def test_the_top_bar_gets_out_of_the_way_while_the_page_is_read():
    """Two failures either side of this, and the rule is the middle of them.

    Pinned, it stood over the page for every screenful and spent 65px of a
    short viewport doing it. Taken out of the flow altogether, the bell and the
    account menu became a scroll to the top of the page rather than controls.

    So it keeps its place in the document -- nothing below has to reserve room
    -- and takes itself off the screen while the reader is going down.
    """
    phone = _phone(MOBILE)
    assert "position: sticky" in _rule(phone, ".topbar")
    assert "translate:" in _rule(phone, ".topbar.is-tucked")


def test_the_tuck_clears_the_bar_and_its_margin():
    """Moving it by its own height alone leaves a sliver of the pane hanging
    under the notch, because the margin above it does not travel."""
    tuck = _rule(_phone(MOBILE), ".topbar.is-tucked")
    assert "-100%" in tuck
    assert "--safe-top" in tuck, "the notch inset has to come off too"


def test_the_tuck_is_a_translate_and_not_a_transform():
    """Nothing on this bar uses transform today. A transform here would be the
    declaration that silently overwrites the first hover or :active state
    anybody adds to it -- the same reason the press zoom is written this way."""
    rule = _rule(_phone(MOBILE), ".topbar.is-tucked")
    assert "transform" not in rule


def test_reduced_motion_still_gets_out_of_the_way():
    """It just does not travel to do it. Removing the tuck instead would give
    that reader the pinned bar back."""
    reduced = _blocks(_code(MOBILE), "@media (prefers-reduced-motion: reduce)")
    joined = "\n".join(reduced)
    assert ".topbar" in joined and "transition: none" in joined
    assert "is-tucked" not in joined, "the bar still tucks; only the slide goes"


def test_the_top_bar_is_a_rounded_rectangle_inset_from_the_edges():
    rule = _rule(_phone(MOBILE), ".topbar")
    assert "border-radius: 26px" in rule
    assert "margin:" in rule
    for token in ("--safe-left", "--safe-right", "--safe-top"):
        assert token in rule, "inset from the phone's real edges, not the viewport"


def test_the_two_bars_have_the_same_radius():
    """They should read as one piece of furniture at two ends of the screen.
    Two radii is two objects."""
    phone = _phone(MOBILE)
    top = re.search(r"border-radius:\s*(\d+px)", _rule(phone, ".topbar")).group(1)
    bottom = re.search(r"border-radius:\s*(\d+px)", _rule(phone, ".bottom-nav")).group(1)
    assert top == bottom


def test_the_floating_bar_is_bordered_all_round():
    """glass.css gives the bar a border-bottom, which is right while it is
    bolted across the top and wrong the moment it floats: there is no edge for
    a single bottom border to sit against."""
    assert "border: 1px solid var(--glass-rim)" in _phone(GLASS)


def test_the_scroll_shadow_no_longer_claims_an_overlap():
    """is-stuck marked the moment the bar began overlapping the page. It never
    does now, so the class must not go on drawing a shadow that says it does."""
    phone = _phone(GLASS)
    assert ".topbar.is-stuck" in phone, "the class is still applied on scroll"
    assert "var(--glass-shadow)" not in phone.split(".topbar.is-stuck")[1].split("}")[0]


def test_the_desktop_bar_is_untouched():
    """On a wide screen it still spans the top and still stays there."""
    outside = _code(MOBILE)
    for block in _blocks(outside, "@media (max-width: 768px)"):
        outside = outside.replace(block, "")
    assert "is-tucked" not in outside, "the tucking is phone-only"

    desktop = _code((CSS / "style.css").read_text(encoding="utf-8"))
    assert "position: sticky" in _rule(desktop, ".topbar")
    assert "border-radius" not in _rule(desktop, ".topbar")


# ---------------------------------------------------------------------------
# Nothing under the tab bar
# ---------------------------------------------------------------------------
COVER = "body:has(.bottom-nav)::after"


def test_nothing_from_the_page_shows_under_the_tab_bar():
    """The bar floats ten pixels off three edges, so the page scrolled past it
    in the gap below it and in the strips either side."""
    rule = _rule(_phone(MOBILE), COVER)
    assert "position: fixed" in rule
    assert "bottom: 0" in rule
    assert "background: var(--bg)" in rule, "opaque, in the page's own colour"
    for token in ("--bottom-nav-gap", "--bottom-nav-h"):
        assert token in rule, "sized from the tokens that place the bar, or the two drift"


def test_the_cover_sits_under_the_bar_and_over_the_page():
    """One number either way and it either hides the bar or hides nothing."""
    phone = _phone(MOBILE)
    cover = int(re.search(r"z-index:\s*(\d+)", _rule(phone, COVER)).group(1))
    nav = int(re.search(r"z-index:\s*(\d+)", _rule(phone, ".bottom-nav")).group(1))
    assert cover < nav, "the bar has to stay visible"
    assert cover > 1, "and the page has to be hidden behind it"


def test_the_cover_cannot_swallow_a_tap():
    assert "pointer-events: none" in _rule(_phone(MOBILE), COVER)


def test_the_cover_exists_only_where_there_is_a_bar():
    """Sign-in and the landing page have no tab bar, and a band of paint across
    the bottom of those is just a band of paint."""
    assert COVER in _code(MOBILE)


def test_the_cover_goes_whenever_the_bar_goes():
    """Three rules hide the bar. A cover left behind by any of them is dead
    colour over whatever replaced it -- a modal, or the keyboard."""
    css = _code(MOBILE)
    assert "body:has(.modal-backdrop:not(.hidden))::after" in css, \
        "the modal rule has to take the cover with it"
    assert ".keyboard-open body:has(.bottom-nav)::after" in css
    landscape = "\n".join(_blocks(css, "orientation: landscape"))
    assert "::after" in landscape


# ---------------------------------------------------------------------------
# What depended on the bar staying put
# ---------------------------------------------------------------------------
def test_the_task_detail_bar_no_longer_hangs_off_a_bar_that_left():
    """It stuck below the top bar. With the top bar scrolling away that pinned
    it 65px down the screen with nothing above it."""
    detail = _rule("\n".join(_blocks(_code(MOBILE), "@media (max-width: 640px)")),
                   ".wk-detail-bar")
    assert "--topbar-h" not in detail
    assert "--safe-top" in detail, "it still has to clear the notch"


def test_the_native_shell_does_not_clear_the_notch_twice():
    """iOS draws the status bar over the web view. The bar's own margin now
    carries that inset, so the padding must not add it a second time."""
    assert re.search(r"\.is-native \.topbar\s*\{\s*padding-top:\s*8px", _code(MOBILE)), \
        "the phone override has to drop the duplicated inset"


# ---------------------------------------------------------------------------
# When it tucks, and when it must not
# ---------------------------------------------------------------------------
APP_JS = Path("app/static/js/app.js").read_text(encoding="utf-8")


def _js(text: str) -> str:
    """JavaScript with its comments removed.

    Line comments too, which _code leaves: the paragraph above this handler
    names every symbol the checks below look for, so without this they would
    pass on the explanation alone.
    """
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))


def _handler() -> str:
    js = _js(APP_JS)
    return js.split("const _topbar = document.getElementById")[1].split("\n}")[0]


def test_it_tucks_going_down_and_comes_back_going_up():
    h = _handler()
    assert "is-tucked" in h
    assert "moved > 0" in h, "direction, not position, is what decides"


def test_it_stays_put_at_the_top_of_a_page():
    """There is nothing to have scrolled past yet, and a bar that vanishes at
    the top of a page reads as a glitch rather than as room."""
    h = _handler()
    assert "TUCK_BELOW" in h
    assert "y > TUCK_BELOW" in h


def test_a_tremble_is_not_a_change_of_direction():
    """A resting thumb and the rubber-band bounce at either end of a phone
    scroll both produce a stream of tiny alternating deltas. Without a
    deadband the bar flickers on every one of them."""
    h = _handler()
    assert "DEADBAND" in h
    assert "Math.abs(moved) < DEADBAND" in h


def test_a_rubber_band_at_the_top_is_not_an_upward_scroll():
    """iOS reports a negative scrollY while overscrolling at the top."""
    assert "Math.max(0, window.scrollY)" in _handler()


def test_it_does_not_tuck_an_open_menu_off_the_screen():
    """Both menus are children of the bar, so tucking it takes an open one
    with it -- the reader watches the thing they just opened leave."""
    h = _handler()
    assert "menuOpen()" in h
    assert "!menuOpen()" in h, "the guard has to be in the toggle, not just defined"
    assert "#user-menu" in h and "#theme-menu" in h


def test_there_is_still_only_one_scroll_listener():
    """The elevation shadow and the tuck are the same gesture read twice."""
    assert _js(APP_JS).count('addEventListener("scroll"') == 1
