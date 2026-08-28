"""The day planner, which exists to not be a fixed routine.

Most of these check that two different days come out different. That is the
whole point of the feature: a plan that says "lunch at 1pm" every day is a
template, and the professor already knows what a template looks like.
"""
from datetime import date

import pytest

from app.services.day_planner import (
    DAY_NAMES,
    _Ev,
    plan_day,
    plan_week,
    to_minutes,
)


class Prefs:
    """A profile with everything set, so each test can override one thing."""

    day_start = "07:00"
    day_end = "22:30"
    lunch_start = "13:00"
    lunch_end = "13:30"
    dinner_start = "20:00"
    dinner_end = "20:45"
    exercise_minutes = 45
    exercise_when = "morning"
    commute_minutes = 0
    study_block_min = 45
    study_block_max = 120
    study_target_minutes = 180
    break_minutes = 15
    focus_period = "morning"
    subject_priorities = "ANN, DBMS"
    working_days = "Mon,Tue,Wed,Thu,Fri"
    timezone = "Asia/Kolkata"


def ev(h1, m1, h2, m2, title="Lecture", location=None):
    return _Ev(h1 * 60 + m1, h2 * 60 + m2, title, title, location, None)


def kinds(plan, kind):
    return [b for b in plan.blocks if b.kind == kind]


MONDAY = date(2026, 8, 24)
TUESDAY = date(2026, 8, 25)


# --------------------------------------------------------------------------
# Nothing may sit on top of a class
# --------------------------------------------------------------------------
def test_nothing_is_ever_scheduled_over_a_fixed_commitment():
    """The one rule with no exception. Everything else in this module is a
    preference; this is the invariant they all bend around."""
    events = [ev(9, 0, 11, 0), ev(12, 0, 13, 30), ev(15, 0, 17, 0)]
    plan = plan_day(MONDAY, events, Prefs())

    fixed = [(b.start, b.end) for b in plan.blocks if b.kind == "fixed"]
    for block in plan.blocks:
        if block.kind == "fixed":
            continue
        for fs, fe in fixed:
            assert block.end <= fs or block.start >= fe, (
                f"{block.title} at {block.start}-{block.end} overlaps a class at {fs}-{fe}"
            )


def test_no_two_blocks_overlap_at_all():
    events = [ev(8, 15, 9, 15), ev(11, 0, 13, 0), ev(14, 0, 16, 30)]
    plan = plan_day(MONDAY, events, Prefs())
    ordered = sorted(plan.blocks, key=lambda b: b.start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.end <= b.start, f"{a.title} runs into {b.title}"


# --------------------------------------------------------------------------
# The day decides, not a template
# --------------------------------------------------------------------------
def test_lunch_moves_when_a_class_sits_across_it():
    """The preferred window is 13:00. A lab from 12:30 to 15:00 makes that
    impossible, so lunch goes to the nearest gap that holds it -- not to a
    hardcoded fallback, and not on top of the lab."""
    plan = plan_day(TUESDAY, [ev(10, 0, 12, 0), ev(12, 30, 15, 0)], Prefs())
    lunch = kinds(plan, "meal")[0]

    assert lunch.title == "Lunch"
    assert lunch.start != 13 * 60
    assert lunch.end <= 12 * 60 + 30, "must finish before the lab starts"
    assert any("Lunch moved" in n for n in plan.notes)


def test_lunch_stays_put_when_the_day_allows_it():
    """The mirror of the test above: a preference that can be honoured is not
    moved for the sake of moving it."""
    plan = plan_day(MONDAY, [ev(9, 0, 11, 0)], Prefs())
    lunch = kinds(plan, "meal")[0]
    assert lunch.start == 13 * 60
    assert not any("Lunch moved" in n for n in plan.notes)


def test_two_different_days_produce_two_different_plans():
    """A Tuesday of afternoon classes and a Wednesday of morning ones must not
    come out as the same day with different headings."""
    busy_afternoon = plan_day(TUESDAY, [ev(13, 0, 17, 0)], Prefs())
    busy_morning = plan_day(MONDAY, [ev(8, 0, 12, 0)], Prefs())

    def shape(p):
        return [(b.kind, b.start, b.end) for b in p.blocks if b.kind != "fixed"]

    assert shape(busy_afternoon) != shape(busy_morning)


def test_a_free_day_gets_more_study_than_a_full_one():
    free = plan_day(MONDAY, [ev(9, 0, 10, 0)], Prefs())
    full = plan_day(MONDAY, [ev(8, 0, 13, 0), ev(14, 0, 19, 0)], Prefs())
    assert sum(b.minutes for b in kinds(free, "study")) >= \
        sum(b.minutes for b in kinds(full, "study"))


# --------------------------------------------------------------------------
# Study blocks
# --------------------------------------------------------------------------
def test_study_blocks_respect_their_minimum_and_maximum():
    plan = plan_day(MONDAY, [ev(11, 0, 12, 0)], Prefs())
    for block in kinds(plan, "study"):
        assert Prefs.study_block_min <= block.minutes <= Prefs.study_block_max


def test_study_stops_at_the_daily_target():
    """A wide-open day is not an invitation to schedule nine hours of work."""
    plan = plan_day(MONDAY, [], Prefs())
    assert sum(b.minutes for b in kinds(plan, "study")) <= Prefs.study_target_minutes


def test_study_blocks_are_named_from_the_subject_priorities():
    plan = plan_day(MONDAY, [], Prefs())
    titles = " ".join(b.title for b in kinds(plan, "study"))
    assert "ANN" in titles


def test_without_priorities_study_is_named_after_what_was_taught():
    """An hour labelled "Study" says nothing. What was actually taught that day
    is the obvious thing to be preparing or marking."""
    prefs = Prefs()
    prefs.subject_priorities = None
    plan = plan_day(MONDAY, [ev(9, 0, 10, 0, title="Thermodynamics")], prefs)
    assert any("Thermodynamics" in b.title for b in kinds(plan, "study"))


def test_short_gaps_are_left_alone():
    """Twenty minutes between two lectures is walking time, not a study slot."""
    events = [ev(9, 0, 10, 0), ev(10, 20, 11, 20), ev(11, 40, 12, 40)]
    plan = plan_day(MONDAY, events, Prefs())
    for block in kinds(plan, "study"):
        assert block.minutes >= Prefs.study_block_min


# --------------------------------------------------------------------------
# Preferences that cannot be met
# --------------------------------------------------------------------------
def test_an_impossible_day_says_so_rather_than_double_booking():
    """Wall-to-wall classes. The plan must come back honest and conflict-free,
    not with lunch stacked on a lecture."""
    plan = plan_day(MONDAY, [ev(7, 0, 22, 30)], Prefs())
    assert plan.notes, "a day with no room must explain itself"
    assert not kinds(plan, "study")
    ordered = sorted(plan.blocks, key=lambda b: b.start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.end <= b.start


def test_exercise_moves_out_of_its_preferred_period_before_being_dropped():
    """Mornings are gone; evening is free. Feasibility beats the preference."""
    plan = plan_day(MONDAY, [ev(6, 0, 13, 0)], Prefs())
    workout = kinds(plan, "exercise")
    assert workout, "exercise should still happen, just later"
    assert workout[0].start >= 13 * 60


def test_exercise_is_left_out_when_it_is_set_to_zero():
    prefs = Prefs()
    prefs.exercise_minutes = 0
    assert not kinds(plan_day(MONDAY, [], prefs), "exercise")


# --------------------------------------------------------------------------
# Travel
# --------------------------------------------------------------------------
def test_travel_is_added_around_a_run_of_classes_not_between_them():
    """Two lectures an hour apart in the same building is one trip in and one
    trip home, not four crossings of the city."""
    prefs = Prefs()
    prefs.commute_minutes = 30
    plan = plan_day(MONDAY, [ev(10, 0, 11, 0, location="Lab 402"),
                             ev(11, 30, 12, 30, location="Lab 402")], prefs)
    travel = kinds(plan, "travel")
    assert len(travel) == 2
    assert travel[0].end == 10 * 60
    assert travel[1].start == 12 * 60 + 30


def test_no_travel_when_the_commute_is_not_set():
    plan = plan_day(MONDAY, [ev(10, 0, 11, 0, location="Lab 402")], Prefs())
    assert not kinds(plan, "travel")


# --------------------------------------------------------------------------
# The week
# --------------------------------------------------------------------------
def test_a_class_outside_waking_hours_still_appears():
    """The window stretches to hold it. Dropping a real commitment because it
    falls outside a preference would be the worst possible failure here."""
    plan = plan_day(MONDAY, [ev(6, 0, 7, 0, title="Early Lab")], Prefs())
    assert any(b.title == "Early Lab" for b in plan.blocks)


def test_a_non_working_day_with_nothing_on_it_is_left_out():
    plans = plan_week([], Prefs(), MONDAY)
    assert all(p.name != "Sunday" for p in plans)


def test_every_planned_day_is_named_correctly():
    for plan in plan_week([], Prefs(), MONDAY):
        assert plan.name == DAY_NAMES[plan.day.weekday()]


@pytest.mark.parametrize("value,expected", [
    ("08:15", 495), ("00:00", 0), ("23:59", 1439), ("9:5", 545), (None, 42), ("nonsense", 42),
])
def test_time_parsing_survives_a_half_filled_profile(value, expected):
    assert to_minutes(value, 42) == expected
