"""
What the rule-based parser makes of ordinary sentences.

This is the parser every prompt falls back to when no AI provider answers,
which in practice is whenever a key is missing, wrong, or the endpoint has
been retired. It was mangling plain phrasing:

    "Add DBMS lab tomorrow from 2 to 4 pm"  ->  "Dbms Lab 2 In Lab 3", 16:00-17:00
    "a project review on 30 August at 3pm"  ->  "A Project Review 30 August", no date
    "I have a seminar on Thursday"          ->  a weekly series
    "ANN lecture"                           ->  "Ann Lecture"
"""
from datetime import date

import pytest

from app.services.ai_service import AIService, _clean_title, _extract_date, _extract_times


@pytest.fixture
def parse():
    svc = AIService()

    def _parse(prompt):
        # The fallback directly: these tests are about the fallback, and going
        # through process_prompt would reach for a provider that may answer.
        return svc._fallback_rule_based(prompt, {"timezone": "Asia/Kolkata"})

    return _parse


def _event(result):
    events = result.get("events") or []
    assert events, f"expected an event, got intent={result.get('intent')}"
    return events[0]


# --- Times -----------------------------------------------------------------

def test_a_range_reads_both_ends(parse):
    """The one that hurt most. Only "4 pm" matched, so it became the start and
    an end was invented an hour later -- a two-hour lab booked as 4-5."""
    e = _event(parse("Add DBMS lab tomorrow from 2 to 4 pm"))
    assert (e["start_time"], e["end_time"]) == ("14:00", "16:00")


def test_a_meridiem_at_either_end_speaks_for_both(parse):
    assert _extract_times("from 2 to 4 pm") == ["14:00", "16:00"]
    assert _extract_times("from 9 am to 11") == ["09:00", "11:00"]
    assert _extract_times("9 am to 11 am") == ["09:00", "11:00"]


def test_twenty_four_hour_times_are_read_as_written(parse):
    e = _event(parse("Faculty meeting from 10:30 to 12:00"))
    assert (e["start_time"], e["end_time"]) == ("10:30", "12:00")


def test_a_single_time_still_gets_an_hour(parse):
    e = _event(parse("I have ANN lecture at 10 AM"))
    assert (e["start_time"], e["end_time"]) == ("10:00", "11:00")


def test_a_bare_number_is_not_guessed_to_be_a_time(parse):
    """Deliberate. A sentence is full of numbers that are not clock times --
    "Lab 3", "Room 204", "2 groups" -- and reading any of them as a time books
    the class at the wrong hour rather than admitting it did not know."""
    assert _extract_times("lecture at 2") == []
    assert _extract_times("DBMS lab in Lab 3") == []
    e = _event(parse("Add DBMS lab tomorrow in Lab 3"))
    assert e["start_time"] == "09:00", "no time said, so the working-day default stands"


def test_a_range_with_no_meridiem_is_read_as_a_working_day(parse):
    """"9 to 5" is not nine in the morning to five in the morning. Only a
    range gets this treatment: both ends are present, so the shape of the
    sentence says these are clock times."""
    assert _extract_times("I teach from 9 to 5") == ["09:00", "17:00"]


def test_noon_and_midnight_are_not_shifted_twice(parse):
    assert _extract_times("at 12 pm")[0] == "12:00"
    assert _extract_times("at 12 am")[0] == "00:00"


# --- Dates -----------------------------------------------------------------

def test_a_written_date_is_not_dropped(parse):
    """It used to vanish silently, so the event landed on today with nothing
    saying anything had been lost."""
    e = _event(parse("I have a project review on 30 August at 3pm"))
    assert e["date"] == _expected("30 August")


def test_both_orders_of_day_and_month_are_read(parse):
    assert _extract_date("on 30 august") == _expected("30 August")
    assert _extract_date("on august 30") == _expected("30 August")
    assert _extract_date("on 30th of august") == _expected("30 August")


def test_an_iso_date_is_taken_as_given(parse):
    assert _extract_date("on 2027-03-14") == "2027-03-14"


def test_a_date_already_past_rolls_to_next_year(parse):
    """"on 3 January" said in December means the January coming."""
    today = date.today()
    gone = today.replace(day=1) if today.month > 1 else None
    if gone is None:
        pytest.skip("no month has passed yet this year")
    parsed = _extract_date(f"on 1 {gone.strftime('%B').lower()}")
    assert parsed is not None
    assert date.fromisoformat(parsed) >= today


def test_a_nonsense_date_is_not_invented(parse):
    assert _extract_date("on 31 february") is None
    assert _extract_date("sometime later") is None


def _expected(text: str) -> str:
    day, month_name = text.split()
    from app.services.ai_service import _MONTHS

    today = date.today()
    month = _MONTHS[month_name.lower()]
    for year in (today.year, today.year + 1):
        candidate = date(year, month, int(day))
        if candidate >= today:
            return candidate.isoformat()
    raise AssertionError


# --- Repeating, or not -----------------------------------------------------

def test_naming_a_weekday_does_not_make_it_weekly(parse):
    """"I have a seminar on Thursday" is one seminar. The old rule turned
    every day-of-week mention into a series, so a single class silently became
    one every week for the rest of term."""
    result = parse("I have a Seminar on Thursday at 11 AM")
    assert result["intent"] == "CREATE_EVENT"
    assert _event(result)["recurrence"] is None
    assert _event(result)["day"] == "Thursday"


def test_saying_every_does_make_it_weekly(parse):
    result = parse("I have DAA lecture every Monday from 9 to 11 am")
    assert result["intent"] == "CREATE_RECURRING_EVENT"
    e = _event(result)
    assert e["recurrence"] == "weekly"
    assert (e["start_time"], e["end_time"]) == ("09:00", "11:00")


# --- Titles ----------------------------------------------------------------

def test_acronyms_survive(parse):
    """Title-casing turned a professor's course codes into words: ANN became
    Ann, DBMS became Dbms."""
    assert _event(parse("I have ANN lecture at 10 AM"))["title"] == "ANN lecture"
    assert _event(parse("Add DBMS lab tomorrow from 2 to 4 pm"))["title"] == "DBMS lab"


@pytest.mark.parametrize("prompt,title", [
    ("I have a Seminar on Thursday at 11 AM", "Seminar"),
    ("Schedule a department meeting next Friday at 2 PM", "department meeting"),
    ("I have a project review on 30 August at 3pm", "project review"),
    ("Add DBMS lab tomorrow from 2 to 4 pm in Lab 3", "DBMS lab"),
])
def test_the_title_is_what_the_thing_is_called(parse, prompt, title):
    assert _event(parse(prompt))["title"] == title


def test_the_title_keeps_nothing_of_the_scaffolding(parse):
    """Every one of these appeared in a real title before: the article, the
    trailing "Next", the date, and the leftover digit from a time range."""
    for junk in ("A ", "An ", "The ", " Next", "30 August", " 2 In "):
        assert junk.strip() not in _clean_title(
            "Schedule a department meeting next Friday at 2 PM on 30 August")


def test_a_title_is_never_empty(parse):
    assert _clean_title("at 10 am tomorrow") == "New Event"


def test_the_location_is_not_left_in_the_title(parse):
    e = _event(parse("Add DBMS lab tomorrow from 2 to 4 pm in Lab 3"))
    assert "Lab 3" not in e["title"]
    assert e["location"] == "Lab 3"
