"""The Work page's own layout contracts.

The illustration in the "Waiting on you" card is positioned out of flow, in
the corner of a card whose heading and caption run the full width of it. That
is a collision waiting to happen and it happened: on a 390px phone the heading
and the caption both ran to x=353 while the clipboard sat from 307 to 353,
straight over the top of both lines. It was there on a 1280px desktop too --
the caption is short enough that nobody saw it, but the box overlapped by 57px
and a longer caption would have shown it.

Absolute positioning takes an element out of the flow; it does not tell the
flow to get out of its way. Something has to reserve the space, and this is
what checks that something still does.
"""
import re
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fresh_db():
    """These tests read a stylesheet; they open no session.

    Overriding the suite-wide reset keeps them from dropping and rebuilding
    the schema once per assertion.
    """
    yield


WORK = Path("app/static/css/work.css").read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Source without block comments, so a check cannot match the paragraph
    that explains it rather than the declaration that implements it."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _blocks(css: str, opener: str) -> list[str]:
    """Brace-matched blocks; these nest, so a regex cannot find their ends."""
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


def _reserve_in(block: str) -> str | None:
    m = re.search(r"--inbox-art-reserve:\s*([^;]+);", block)
    return m.group(1).strip() if m else None


def _outside_media(css: str) -> str:
    """Everything that is not inside an @media block.

    Splitting on the first "@media" was wrong: this stylesheet has media
    queries above the rule being looked for, so the base declaration fell on
    the far side of the split and read as absent.
    """
    out, i = [], 0
    while True:
        m = re.search(r"@media[^{]*\{", css[i:])
        if not m:
            out.append(css[i:])
            return "".join(out)
        start = i + m.start()
        out.append(css[i:start])
        depth, j = 1, i + m.end()
        while depth and j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        i = j


CODE = _code(WORK)
BASE = _outside_media(CODE)
PHONE = "\n".join(_blocks(CODE, "@media (max-width: 768px)"))
NARROW = "\n".join(_blocks(CODE, "@media (max-width: 380px)"))


def test_the_caption_stops_before_the_illustration():
    """The bug, stated as a rule: something must reserve the corner."""
    assert "--inbox-art-reserve" in CODE, "the space has to be reserved somewhere"
    assert re.search(
        r"\.wk-inbox:has\(\.wk-inbox-art:not\(\.hidden\)\)\s+\.wk-card-titles\s*\{[^}]*"
        r"padding-right:\s*var\(--inbox-art-reserve\)", CODE), \
        "the titles have to give up the width the illustration occupies"


def test_the_reserve_is_keyed_on_the_picture_and_not_on_the_inbox():
    """has-items and a visible illustration are toggled together today, but
    the class means "there is something in the inbox" while the padding needs
    to know "there is a picture in the corner" -- one refactor from differing.
    """
    rule = re.search(r"([^\n{}]*)\s*\{[^}]*padding-right:\s*var\(--inbox-art-reserve\)",
                     CODE).group(1)
    assert "wk-inbox-art" in rule, "key it on the art itself"
    assert "has-items" not in rule


def test_a_narrower_glyph_reserves_less():
    """The illustration shrinks and moves in on a phone. A reserve that did
    not follow it would take width the caption could have used."""
    base = _reserve_in(BASE)
    phone = _reserve_in(PHONE)
    assert base and phone, "both widths have to say what they reserve"
    assert int(phone.rstrip("px")) < int(base.rstrip("px"))


def test_no_space_is_held_for_a_picture_that_is_not_there():
    """Below 380px the illustration is dropped entirely. Reserving for it then
    is a 58px indent on the narrowest screen in the range, for nothing."""
    assert ".wk-inbox-art { display: none; }" in NARROW
    assert _reserve_in(NARROW) == "0px"


def test_the_reserve_is_wider_than_the_glyph_it_clears():
    """It has to cover the glyph, the distance it sits off the card's edge and
    a gap. Measured at 390px: a 46px glyph 16px in, so 58px leaves 12px of
    daylight. A reserve merely equal to the glyph would still touch it.
    """
    phone_reserve = int(_reserve_in(PHONE).rstrip("px"))
    offset = int(re.search(r"\.wk-inbox-art\s*\{[^}]*right:\s*(\d+)px", PHONE).group(1))
    glyph = 46  # measured in the browser at font-size 2.1rem
    assert phone_reserve >= offset + glyph - 22, (
        "the reserve has to clear the glyph and the card's own padding"
    )
