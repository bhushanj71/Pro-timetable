"""Builds one realistic plan per day, around the timetable that already exists.

The point of this module is what it refuses to do. It does not lay a routine
over the week and hope it fits. Each day is planned on its own, from that day's
actual commitments outward, so a Tuesday with classes 10-4 and a Wednesday with
classes 8-12 come out looking nothing like each other -- because they are not
alike.

Everything the professor set in their profile is treated as a *preference*.
Lunch has a preferred window, not a time. When a class sits across it, lunch
moves to the nearest gap that can hold it. When the day is so full that it
cannot be held at all, the plan says so instead of double-booking. A schedule
that cannot be lived is worse than one that admits what it could not fit.

Order of precedence, highest first:

  1. Fixed commitments from the timetable -- never moved, never overlapped
  2. Travel either side of anything with a location
  3. Meals, within their preferred window where possible
  4. Exercise, in its preferred part of the day
  5. Study, filling what is left towards a daily target
  6. Breaks, so nothing demanding runs straight into something else
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.nlp_dates import ensure_aware_utc, get_tz

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Where the day divides, for "morning" / "afternoon" / "evening" preferences.
MORNING_END = 12 * 60
AFTERNOON_END = 17 * 60

# A gap shorter than this is left alone. Filling every crevice produces a plan
# nobody follows -- five minutes between two lectures is walking time.
MIN_USEFUL_GAP = 20


def to_minutes(hhmm: str | None, fallback: int) -> int:
    """"HH:MM" as minutes from midnight, tolerant of an empty profile field."""
    if not hhmm:
        return fallback
    try:
        hours, _, minutes = hhmm.partition(":")
        return int(hours) * 60 + int(minutes or 0)
    except (TypeError, ValueError):
        return fallback


def hhmm(minutes: int) -> str:
    minutes = max(0, min(24 * 60, minutes))
    suffix = "AM" if minutes < 12 * 60 else "PM"
    hour = minutes // 60 % 12 or 12
    return f"{hour}:{minutes % 60:02d} {suffix}"


def span(start: int, end: int) -> str:
    return f"{hhmm(start)} – {hhmm(end)}"


@dataclass
class Block:
    start: int
    end: int
    title: str
    kind: str            # fixed | travel | meal | exercise | study | break
    detail: str = ""

    @property
    def minutes(self) -> int:
        return self.end - self.start

    @property
    def is_fixed(self) -> bool:
        return self.kind in ("fixed", "travel")


@dataclass
class DayPlan:
    day: date
    name: str
    blocks: list[Block] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in self.blocks:
            out[b.kind] = out.get(b.kind, 0) + b.minutes
        return out


# --------------------------------------------------------------------------
# Free-space arithmetic
# --------------------------------------------------------------------------
def _free_gaps(taken: list[Block], window_start: int, window_end: int) -> list[tuple[int, int]]:
    """What is left of the waking day once the immovable things are placed."""
    gaps: list[tuple[int, int]] = []
    cursor = window_start
    for b in sorted(taken, key=lambda x: x.start):
        if b.start > cursor:
            gaps.append((cursor, min(b.start, window_end)))
        cursor = max(cursor, b.end)
        if cursor >= window_end:
            break
    if cursor < window_end:
        gaps.append((cursor, window_end))
    return [(s, e) for s, e in gaps if e - s >= MIN_USEFUL_GAP]


def _place_near(gaps: list[tuple[int, int]], preferred_start: int, duration: int) -> int | None:
    """The start time closest to where this was wanted that actually fits.

    This is the whole of "move lunch to the nearest suitable period": not the
    first free slot of the day, and not a fixed fallback time -- the one that
    disturbs the preference least.
    """
    best: tuple[int, int] | None = None       # (cost, start)
    for gs, ge in gaps:
        if ge - gs < duration:
            continue
        start = max(gs, min(preferred_start, ge - duration))
        cost = abs(start - preferred_start)
        if best is None or cost < best[0]:
            best = (cost, start)
    return None if best is None else best[1]


def _consume(gaps: list[tuple[int, int]], start: int, end: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for gs, ge in gaps:
        if end <= gs or start >= ge:
            out.append((gs, ge))
            continue
        if gs < start:
            out.append((gs, start))
        if ge > end:
            out.append((end, ge))
    return [(s, e) for s, e in out if e - s >= MIN_USEFUL_GAP]


def _period_of(minute: int) -> str:
    if minute < MORNING_END:
        return "morning"
    return "afternoon" if minute < AFTERNOON_END else "evening"


# --------------------------------------------------------------------------
# The plan for one day
# --------------------------------------------------------------------------
def plan_day(day: date, events, prefs) -> DayPlan:
    """`prefs` is the User row; only its planning fields are read."""
    plan = DayPlan(day=day, name=DAY_NAMES[day.weekday()])

    window_start = to_minutes(getattr(prefs, "day_start", None), 7 * 60)
    window_end = to_minutes(getattr(prefs, "day_end", None), 22 * 60 + 30)

    # --- 1. Fixed commitments ------------------------------------------
    fixed: list[Block] = []
    for e in events:
        detail = " · ".join(b for b in (e.location, e.faculty) if b)
        fixed.append(Block(e.start_min, e.end_min, e.subject or e.title, "fixed", detail))
    fixed.sort(key=lambda b: b.start)

    # A commitment outside the stated waking hours still has to appear, so the
    # window stretches rather than the class being dropped.
    for b in fixed:
        window_start = min(window_start, b.start)
        window_end = max(window_end, b.end)

    # --- 2. Travel ------------------------------------------------------
    commute = int(getattr(prefs, "commute_minutes", 0) or 0)
    travel: list[Block] = []
    if commute:
        # One trip out and one back for a contiguous run on campus, not a
        # round trip between consecutive lectures in the same building.
        runs: list[list[Block]] = []
        for b in [x for x in fixed if x.detail]:
            if runs and b.start - runs[-1][-1].end <= commute * 2:
                runs[-1].append(b)
            else:
                runs.append([b])
        for run in runs:
            travel.append(Block(max(0, run[0].start - commute), run[0].start, "Travel", "travel"))
            travel.append(Block(run[-1].end, run[-1].end + commute, "Travel home", "travel"))
        window_start = min(window_start, *(t.start for t in travel)) if travel else window_start
        window_end = max(window_end, *(t.end for t in travel)) if travel else window_end

    placed: list[Block] = fixed + travel
    gaps = _free_gaps(placed, window_start, window_end)

    def commit(block: Block) -> None:
        nonlocal gaps
        placed.append(block)
        gaps = _consume(gaps, block.start, block.end)

    # --- 3. Meals -------------------------------------------------------
    for label, start_attr, end_attr, default in (
        ("Lunch", "lunch_start", "lunch_end", (13 * 60, 13 * 60 + 30)),
        ("Dinner", "dinner_start", "dinner_end", (20 * 60, 20 * 60 + 45)),
    ):
        pref_start = to_minutes(getattr(prefs, start_attr, None), default[0])
        pref_end = to_minutes(getattr(prefs, end_attr, None), default[1])
        duration = max(15, pref_end - pref_start)
        at = _place_near(gaps, pref_start, duration)
        if at is None:
            plan.notes.append(f"No clear gap for {label.lower()} — the day is full through its usual window.")
            continue
        if at != pref_start:
            plan.notes.append(
                f"{label} moved to {hhmm(at)} — {hhmm(pref_start)} was taken by a fixed commitment."
            )
        commit(Block(at, at + duration, label, "meal"))

    # --- 4. Exercise ----------------------------------------------------
    minutes = int(getattr(prefs, "exercise_minutes", 0) or 0)
    if minutes:
        want = getattr(prefs, "exercise_when", "morning") or "morning"
        anchor = {"morning": window_start + 30, "afternoon": 15 * 60, "evening": 18 * 60}.get(want, window_start + 30)
        preferred = [g for g in gaps if _period_of(g[0]) == want and g[1] - g[0] >= minutes]
        at = _place_near(preferred or gaps, anchor, minutes)
        if at is None:
            plan.notes.append("No room for exercise today without displacing something fixed.")
        else:
            if _period_of(at) != want:
                plan.notes.append(f"Exercise moved to the {_period_of(at)} — the {want} was committed.")
            commit(Block(at, at + minutes, "Exercise", "exercise"))

    # --- 5. Study -------------------------------------------------------
    target = int(getattr(prefs, "study_target_minutes", 0) or 0)
    block_min = max(15, int(getattr(prefs, "study_block_min", 45) or 45))
    block_max = max(block_min, int(getattr(prefs, "study_block_max", 120) or 120))
    rest = max(0, int(getattr(prefs, "break_minutes", 15) or 15))
    focus = getattr(prefs, "focus_period", "morning") or "morning"

    subjects = [s.strip() for s in (getattr(prefs, "subject_priorities", "") or "").split(",") if s.strip()]
    if not subjects:
        # Whatever was taught today is the obvious thing to prepare or mark.
        subjects = list(dict.fromkeys(b.title for b in fixed)) or ["Study"]

    scheduled = 0
    subject_i = 0
    # The focus period first, then the rest of the day. Within each, earliest
    # first, because a plan that starts later than it needs to is a plan that
    # slips.
    ordered = sorted(gaps, key=lambda g: (_period_of(g[0]) != focus, g[0]))
    for gs, ge in ordered:
        cursor = gs
        while scheduled < target and ge - cursor >= block_min:
            length = min(block_max, ge - cursor, target - scheduled)
            if length < block_min:
                break
            title = f"Study — {subjects[subject_i % len(subjects)]}"
            subject_i += 1
            commit(Block(cursor, cursor + length, title, "study"))
            scheduled += length
            cursor += length
            # A break only where something follows it inside this same gap.
            if rest and scheduled < target and ge - (cursor + rest) >= block_min:
                commit(Block(cursor, cursor + rest, "Break", "break"))
                cursor += rest
        if scheduled >= target:
            break

    if target and scheduled == 0:
        plan.notes.append("No study time today — the day has no free block long enough to be worth starting.")
    elif target and scheduled < target:
        short = target - scheduled
        plan.notes.append(
            f"Study came to {scheduled // 60}h {scheduled % 60:02d}m of the {target // 60}h "
            f"target — {short // 60}h {short % 60:02d}m short, which is what the day had room for."
        )

    plan.blocks = sorted(placed, key=lambda b: (b.start, b.end))
    return plan


# --------------------------------------------------------------------------
@dataclass
class _Ev:
    start_min: int
    end_min: int
    title: str
    subject: str | None
    location: str | None
    faculty: str | None


def group_by_day(events, tz_name: str) -> dict[date, list[_Ev]]:
    """Events bucketed by their *local* day.

    Stored times are UTC. Reading the day off them puts a 00:30 class on the
    day before, and an early-morning class in the wrong period entirely.
    """
    tz = get_tz(tz_name or "UTC")
    out: dict[date, list[_Ev]] = {}
    for e in events:
        start = ensure_aware_utc(e.start_datetime).astimezone(tz)
        end = ensure_aware_utc(e.end_datetime).astimezone(tz)
        out.setdefault(start.date(), []).append(
            _Ev(
                start_min=start.hour * 60 + start.minute,
                end_min=end.hour * 60 + end.minute,
                title=e.title,
                subject=e.subject,
                location=e.location,
                faculty=getattr(e, "faculty", None),
            )
        )
    for day in out.values():
        day.sort(key=lambda x: x.start_min)
    return out


def plan_week(events, prefs, start: date, days: int = 7) -> list[DayPlan]:
    """One independently-optimised plan per day of the period.

    A day with nothing on it and nothing to place is left out entirely -- an
    empty table under a heading tells the reader nothing they did not know.
    """
    by_day = group_by_day(events, getattr(prefs, "timezone", "UTC"))
    working = {d.strip().lower()[:3] for d in (getattr(prefs, "working_days", "") or "").split(",") if d.strip()}

    plans: list[DayPlan] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        todays = by_day.get(day, [])
        is_working = DAY_NAMES[day.weekday()][:3].lower() in working
        if not todays and not is_working:
            continue
        plan = plan_day(day, todays, prefs)
        if plan.blocks:
            plans.append(plan)
    return plans
