"""The theme control, and where the text-size control went.

The theme used to be a popover: three options and a text-size slider inside
it. It is now a toggle -- one press, one change -- with the incoming palette
revealed by a circle growing out of the button that was pressed, which is
Skiper26's move. A toggle has no inside, so the slider moved out to the
account menu.

Two things here are worth more than the rest.

The reveal is scoped under .vt-theme. mobile.css already animates
::view-transition-old/new(root) for page navigation, and a same-document
transition uses those same pseudo-elements: unscoped, pressing the toggle
would slide the page sideways like a followed link.

And "match system" had to survive. Two states cannot express "follow the OS",
so a toggle on its own silently drops a real setting. It moved rather than
went.
"""
import re
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fresh_db():
    """These read files; they open no session."""
    yield


THEME_CSS = Path("app/static/css/theme.css").read_text(encoding="utf-8")
MOBILE_CSS = Path("app/static/css/mobile.css").read_text(encoding="utf-8")
THEME_JS = Path("app/static/js/theme.js").read_text(encoding="utf-8")
BASE = Path("app/templates/base.html").read_text(encoding="utf-8")


def _css(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _js(text: str) -> str:
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))


def _jinja(text: str) -> str:
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


# ---------------------------------------------------------------------------
# One press, one change
# ---------------------------------------------------------------------------
def test_the_theme_control_is_a_toggle_and_not_a_menu():
    assert 'id="theme-menu"' not in _jinja(BASE), "the popover is gone"
    assert "renderThemeMenu" not in _js(THEME_JS), "and so is what filled it"
    assert 'id="theme-btn"' in BASE


def test_the_palette_arrives_through_a_view_transition():
    assert "document.startViewTransition" in _js(THEME_JS)


def test_the_reveal_cannot_hijack_a_page_navigation():
    """The scope is the whole reason this works. Both animations target
    ::view-transition-*(root); only the class keeps them apart."""
    css = _css(THEME_CSS)
    assert ":root.vt-theme::view-transition-new(root)" in css
    assert "::view-transition-new(root)" in _css(MOBILE_CSS), \
        "navigation still animates the same pseudo-element"
    for line in css.splitlines():
        if "::view-transition-new(root)" in line:
            assert "vt-theme" in line, f"unscoped rule would catch navigation too: {line}"


def test_the_circle_starts_at_the_button_that_was_pressed():
    """A circle from the middle of the screen is a different effect, and says
    the change came from the page rather than from the control."""
    js = _js(THEME_JS)
    assert "getBoundingClientRect" in js
    for prop in ("--vt-x", "--vt-y", "--vt-r"):
        assert prop in js, f"{prop} has to be handed to the stylesheet"
        assert prop in _css(THEME_CSS), f"{prop} has to be read by the keyframes"


def test_the_circle_reaches_the_furthest_corner():
    """Sized from the near edge instead and the old palette is left showing in
    a corner for the rest of the session."""
    js = _js(THEME_JS)
    assert "Math.hypot" in js
    assert "Math.max(x, innerWidth - x)" in js
    assert "Math.max(y, innerHeight - y)" in js


def test_the_transition_class_is_dropped_even_if_the_browser_abandons_it():
    """A skipped transition still has to clean up: the class left behind would
    make every later page navigation animate as a circle."""
    js = _js(THEME_JS)
    assert "vt.finished.finally(" in js, "finally, not then -- a rejection must still clean up"


def test_the_icon_turns_a_half_circle_each_press_and_keeps_going():
    """Resetting to zero between presses leaves the glyph upside down every
    other time."""
    js = _js(THEME_JS)
    assert "spin += 180" in js
    assert "rotate 500ms" in _css(THEME_CSS) or "rotate 500ms" in THEME_CSS


def test_the_turn_is_a_rotate_and_not_a_transform():
    """Same reason as everywhere else in this codebase: a transform is the one
    declaration that silently overwrites whatever else is on the element."""
    js = _js(THEME_JS)
    assert "style.rotate" in js
    assert "style.transform" not in js


def test_writing_the_glyph_does_not_destroy_the_thing_that_turns():
    """btn.textContent would delete the span the rotation is applied to, and
    the turn would stop working after the first press."""
    js = _js(THEME_JS)
    assert "btn.textContent" not in js
    assert 'getElementById("theme-icon")' in js
    assert 'id="theme-icon"' in BASE


def test_a_browser_without_view_transitions_still_changes_theme():
    """The circle is the decoration; the palette is the feature."""
    js = _js(THEME_JS)
    assert "!document.startViewTransition" in js
    branch = js.split("!document.startViewTransition")[1][:260]
    assert "applyTheme(next" in branch


def test_reduced_motion_skips_the_sweep_but_not_the_change():
    js = _js(THEME_JS)
    assert "prefers-reduced-motion" in js
    reduced = [b for b in re.findall(
        r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", _css(THEME_CSS), re.S)]
    assert any("view-transition-new" in b for b in reduced), \
        "the sweep has to be switched off in the stylesheet too"


# ---------------------------------------------------------------------------
# What moved out of it
# ---------------------------------------------------------------------------
def test_the_text_size_control_is_outside_the_toggle():
    html = _jinja(BASE)
    assert 'id="um-appearance"' in html
    # Bounded by the menu's own markers. /profile is also a sidebar link, so
    # the first occurrence of it is nowhere near this menu.
    menu_at = html.index('id="user-menu"')
    end_at = html.index('id="um-logout"')
    appearance_at = html.index('id="um-appearance"')
    assert menu_at < appearance_at < end_at, "it belongs inside the account menu"
    assert "renderAppearance" in _js(THEME_JS)
    assert "font-size-range" in THEME_JS


def test_following_the_system_was_moved_and_not_dropped():
    """Two states cannot express three. Losing this would be a setting deleted
    under cover of a redesign."""
    js = _js(THEME_JS)
    assert 'id="um-system-theme"' in js, "the option has to be rendered"
    # The option's own handler, not just the string anywhere in the file:
    # applyTheme("system") also appears in the OS-preference listener, so a
    # bare substring check passes even with the button wired to nothing.
    handler = js.split('getElementById("um-appearance")?.addEventListener("click"')[1][:320]
    assert "um-system-theme" in handler and 'applyTheme("system"' in handler, \
        "pressing it has to actually hand control back to the OS"


def test_the_toggle_commits_to_one_of_two_states():
    """It reads what is actually on screen, so pressing it while following the
    system gives the opposite of what the reader can see -- not the opposite of
    the word "system", which would be a no-op half the time."""
    js = _js(THEME_JS)
    assert 'resolvedTheme() === "dark" ? "light" : "dark"' in js
