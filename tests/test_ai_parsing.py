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


# --- How people and dictation engines actually write times -----------------

def test_dotted_meridiems_are_read(parse):
    """Reported from the app: "I have DSV lecture at 12:15 p.m." Every pattern
    matched (am|pm) and none of them matched "p.m.", so "at 3 p.m." found no
    time at all and the lecture was booked at the 09:00 working-day default --
    six hours out, with "p.m." left sitting in the title."""
    assert _extract_times("at 3 p.m.") == ["15:00"]
    assert _extract_times("at 3 P.M.") == ["15:00"]
    assert _extract_times("at 12:15 p.m.") == ["12:15"]
    assert _extract_times("at 10 a.m.") == ["10:00"]
    assert _extract_times("from 2 p.m. to 4 p.m.") == ["14:00", "16:00"]


def test_a_dotted_meridiem_does_not_survive_into_the_title(parse):
    e = _event(parse("I have a seminar at 3 p.m."))
    assert e["title"] == "seminar"
    assert (e["start_time"], e["end_time"]) == ("15:00", "16:00")


def test_a_sentence_ending_full_stop_is_not_eaten(parse):
    assert _clean_title("I have a seminar at 3 p.m.") == "seminar"


# --- Rooms said as one word ------------------------------------------------

@pytest.mark.parametrize("prompt,title,location", [
    ("I have DSV lecture at 12:15 p.m. in the classroom", "DSV lecture", "Classroom"),
    ("Meeting at 10 a.m. in the seminar hall", "Meeting", "Seminar Hall"),
    ("Viva in auditorium at 11 am", "Viva", "Auditorium"),
    ("Tutorial in classroom 5 at 2 pm", "Tutorial", "Classroom 5"),
    ("ANN lecture at 9 am in room 302", "ANN lecture", "Room 302"),
])
def test_one_word_rooms_are_a_location_not_part_of_the_title(parse, prompt, title, location):
    """The old rule only knew multi-word rooms ("in room 5"), so "in the
    classroom" stayed in the title and no location was recorded at all."""
    e = _event(parse(prompt))
    assert e["title"] == title
    assert e["location"] == location


def test_the_article_is_not_part_of_the_room_name(parse):
    """A class is held in the classroom, not in "The Classroom"."""
    e = _event(parse("Lecture at 2 pm in the classroom"))
    assert e["location"] == "Classroom"


def test_a_room_does_not_swallow_the_next_clause(parse):
    """"on classroom in the CS department" was captured as "Classroom In" --
    the suffix took whatever word came next. A designator is a number or a
    single letter."""
    e = _event(parse("I have DSV lecture at 12:15 p.m. on classroom in the CS department"))
    assert e["location"] == "Classroom"
    assert "In" not in (e["location"] or "")


# --- Saying why the weaker parser is answering -----------------------------

def test_the_fallback_note_distinguishes_missing_from_broken(parse):
    """It always claimed "no AI provider configured", which is a lie exactly
    when one is configured and failing -- the case that needs acting on."""
    from app.services import ai_service

    original = dict(ai_service.LAST_PROVIDER_ERROR)
    try:
        ai_service.LAST_PROVIDER_ERROR["error"] = None
        assert "no AI provider configured" in ai_service._fallback_note()

        ai_service.LAST_PROVIDER_ERROR["error"] = "HTTPStatusError: 410 Gone"
        note = ai_service._fallback_note()
        assert "not responding" in note
        assert "no AI provider configured" not in note
    finally:
        ai_service.LAST_PROVIDER_ERROR.update(original)


# --- What gets read back out loud ------------------------------------------

def test_the_summary_describes_the_schedule_not_the_parser(auth_client):
    """The spoken read-back is response.summary, and the parser's note about
    itself was being prepended to it -- so a voice command opened with "the AI
    provider is configured but not responding, so this reading is rougher than
    usual" before it ever mentioned the lecture.

    The note is still on the response, and still shown for typed commands. It
    is simply not part of the sentence.
    """
    resp = auth_client.post(
        "/api/ai/process-prompt",
        json={"prompt": "I have DSV lecture at 12:15 p.m. in the classroom"},
    ).json()

    summary = resp["summary"]
    assert "DSV lecture" in summary
    for parser_talk in ("fallback", "AI provider", "not responding", "rougher than usual"):
        assert parser_talk not in summary, f"{parser_talk!r} is about the parser, not the schedule"

    # Still available to whoever wants it -- just not in what is spoken.
    assert "notes" in resp["extraction"]
