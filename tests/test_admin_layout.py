"""Two layout faults found on the admin page, one of which was not local to it.

The summary tiles carried no rules at all -- not .stat-tile, not .value, not
.label. The box came from .card and both lines fell back to plain block
defaults, so a count and its caption sat flush left at the same size, reading
as two lines of text rather than as a figure.

The second is the more interesting one. Every container that shows a loading
animation is marked .is-loading-block, and that class carried layout: a
column-centring flex box with 26px of padding, so the spinner would not be
jammed against the top of an empty card. Nothing ever removed the class. The
comment in app.js said "whatever renders into the container replaces it",
which is true of the contents and false of the class -- so the spinner's
layout went on applying to real content for the life of the page.

On the admin page that shrink-wrapped the college row to its own width, left
it floating in the middle of a card whose heading is hard left, and cancelled
the margin-left:auto that pushes Edit, Members and Manage to the right edge:
there is no free space to push into in a box that has shrunk to fit. The
reminders and tasks lists carry the same class and had the same fault.
"""
import re
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fresh_db():
    """These read stylesheets and templates; they open no session."""
    yield


CSS = Path("app/static/css")
STYLE = (CSS / "style.css").read_text(encoding="utf-8")
TEMPLATES = Path("app/templates")


def _code(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", _code(css), re.S)
    assert m, f"{selector} should be a rule"
    return m.group(1)


def _px(rule: str, prop: str) -> float:
    m = re.search(prop + r":\s*([\d.]+)rem", rule)
    if m:
        return float(m.group(1)) * 16
    m = re.search(prop + r":\s*([\d.]+)px", rule)
    assert m, f"no {prop} in {rule!r}"
    return float(m.group(1))


# ---------------------------------------------------------------------------
# The summary tiles
# ---------------------------------------------------------------------------
def test_the_tiles_are_centred():
    rule = _rule(STYLE, ".stat-tile")
    assert "text-align: center" in rule
    assert "align-items: center" in rule, "across"
    assert "justify-content: center" in rule, "and down"


def test_the_tiles_are_centred_down_as_well_as_across():
    """The grid stretches every tile to the tallest in its row, and a caption
    that wraps on a narrow screen makes one taller than the rest. Content
    pinned to the top would then sit at a different height in each tile.
    """
    rule = _rule(STYLE, ".stat-tile")
    assert "display: flex" in rule and "flex-direction: column" in rule


def test_the_count_reads_as_a_figure_and_not_as_a_line_of_text():
    """It was the same size as its own caption, which is what made a tile of
    two centred lines look like a paragraph."""
    value = _px(_rule(STYLE, ".stat-tile .value"), "font-size")
    label = _px(_rule(STYLE, ".stat-tile .label"), "font-size")
    assert value > label * 1.8, f"{value}px figure over a {label}px caption is not a hierarchy"


def test_a_column_of_counts_does_not_shuffle_sideways():
    """Proportional digits are different widths, so a centred count jitters
    as the number changes."""
    assert "tabular-nums" in _rule(STYLE, ".stat-tile .value")


# ---------------------------------------------------------------------------
# The loading container, which is not only the admin page's problem
# ---------------------------------------------------------------------------
def test_the_loading_layout_applies_only_while_the_loader_is_there():
    """The bug, as a rule. The class stays on the element for the life of the
    page -- nothing removes it -- so the layout has to stop applying by itself
    once real content has replaced the spinner.
    """
    assert ".is-loading-block:has(.loader)" in _code(STYLE), \
        "scope it to the loader, or it styles whatever replaced the loader"


def test_the_bare_class_imposes_no_layout():
    """If .is-loading-block still carried these on its own, scoping the other
    rule would have changed nothing."""
    bare = re.search(r"(?<!\)\s)\.is-loading-block\s*\{(.*?)\}", _code(STYLE), re.S)
    if bare:
        body = bare.group(1)
        for prop in ("display: flex", "align-items: center", "padding: 26px"):
            assert prop not in body, f"{prop} must not apply without a loader present"


def test_the_loader_still_gets_room_to_breathe():
    """The class exists so a spinner is not jammed against the top of an empty
    card. Fixing the overreach must not take that away."""
    rule = _rule(STYLE, ".is-loading-block:has(.loader)")
    assert "display: flex" in rule
    assert "align-items: center" in rule
    assert "justify-content: center" in rule
    assert "padding: 26px" in rule


@pytest.mark.parametrize("template,container", [
    ("admin.html", "ad-college-list"),
    ("reminders.html", "reminders-list"),
    ("tasks.html", "tasks-list"),
])
def test_every_page_using_the_class_is_covered(template, container):
    """Three containers had this, not one. A fix in the renderer would have
    needed doing three times and remembering a fourth."""
    html = (TEMPLATES / template).read_text(encoding="utf-8")
    assert f'id="{container}"' in html
    assert "is-loading-block" in html


def test_the_class_is_not_removed_in_javascript():
    """This documents why the fix is in CSS. If a renderer ever does start
    removing the class, the scoping stays correct anyway -- but the reason
    for it should be findable.
    """
    js = Path("app/static/js").glob("*.js")
    removals = [p.name for p in js
                if 'classList.remove("is-loading-block")' in p.read_text(encoding="utf-8")]
    assert not removals, (
        f"{removals} now removes the class; the CSS scoping is still correct, "
        "but this test's premise has changed and the comment should be updated"
    )
