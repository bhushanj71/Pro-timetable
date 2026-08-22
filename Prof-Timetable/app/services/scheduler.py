"""
Free-time detection and constraint-based weekly timetable generation.
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import Event
from app.schemas import SubjectRequirement, TimetableGenerateRequest
from app.services.nlp_dates import combine, ensure_aware_utc

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def find_free_slots(
    db: Session,
    user_id: str,
    target_date: date,
    duration_minutes: int,
    tz_name: str,
    day_start: time = time(9, 0),
    day_end: time = time(17, 0),
) -> list[tuple[datetime, datetime]]:
    """Return free windows of at least duration_minutes on target_date, in the professor's timezone."""
    day_start_dt = combine(target_date, day_start, tz_name)
    day_end_dt = combine(target_date, day_end, tz_name)

    events = (
        db.query(Event)
        .filter(
            Event.user_id == user_id,
            Event.is_cancelled.is_(False),
            Event.start_datetime < day_end_dt,
            Event.end_datetime > day_start_dt,
        )
        .order_by(Event.start_datetime)
        .all()
    )

    # SQLite drops tzinfo on read-back even for DateTime(timezone=True) columns,
    # so normalize every value to aware UTC before comparing.
    busy = [
        (max(ensure_aware_utc(e.start_datetime), day_start_dt), min(ensure_aware_utc(e.end_datetime), day_end_dt))
        for e in events
    ]
    busy.sort()

    free: list[tuple[datetime, datetime]] = []
    cursor = day_start_dt
    for start, end in busy:
        if start > cursor:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < day_end_dt:
        free.append((cursor, day_end_dt))

    return [(s, e) for s, e in free if (e - s) >= timedelta(minutes=duration_minutes)]


class TimetableConflictError(Exception):
    pass


def generate_timetable(request: TimetableGenerateRequest) -> dict:
    """
    Greedy constraint-based generator: for each subject, place the required
    number of weekly lectures into free slots, respecting working hours,
    lunch break, avoid-after cutoff, and preferred days/times when given.
    Returns a day -> list[slot] structure plus any subjects that couldn't be
    fully scheduled.
    """
    working_days = request.working_days
    day_start = _parse_hhmm(request.working_hours_start)
    day_end = _parse_hhmm(request.working_hours_end)
    lunch_start = _parse_hhmm(request.lunch_start) if request.lunch_start else None
    lunch_end = _parse_hhmm(request.lunch_end) if request.lunch_end else None
    avoid_after = _parse_hhmm(request.avoid_after) if request.avoid_after else day_end

    # Build the raw slot grid (HH:MM tuples) per day, honoring lunch + avoid_after
    def build_day_slots(slot_minutes: int) -> list[time]:
        slots = []
        cursor = datetime.combine(date.today(), day_start)
        end_dt = datetime.combine(date.today(), min(day_end, avoid_after))
        while cursor + timedelta(minutes=slot_minutes) <= end_dt:
            slot_start = cursor.time()
            slot_end = (cursor + timedelta(minutes=slot_minutes)).time()
            overlaps_lunch = (
                lunch_start and lunch_end and not (slot_end <= lunch_start or slot_start >= lunch_end)
            )
            if not overlaps_lunch:
                slots.append(slot_start)
            cursor += timedelta(minutes=slot_minutes)
        return slots

    # grid[day][slot_start] = subject_name | None
    grid: dict[str, dict[str, str | None]] = {}
    day_slot_cache: dict[int, list[time]] = {}

    unscheduled: list[dict] = []

    for subject in request.subjects:
        slot_minutes = subject.duration_minutes
        if slot_minutes not in day_slot_cache:
            day_slot_cache[slot_minutes] = build_day_slots(slot_minutes)
        candidate_slots = day_slot_cache[slot_minutes]

        preferred_days = subject.preferred_days or working_days
        placements_needed = subject.lectures_per_week
        placed = 0

        # spread lectures across distinct days first
        ordered_days = [d for d in preferred_days if d in working_days] + [
            d for d in working_days if d not in preferred_days
        ]

        for day in ordered_days:
            if placed >= placements_needed:
                break
            grid.setdefault(day, {t.strftime("%H:%M"): None for t in day_slot_cache.get(60, build_day_slots(60))})

            slots_for_day = candidate_slots
            preferred_start = (
                _parse_hhmm(subject.preferred_start_time) if subject.preferred_start_time else None
            )
            ordered_slots = sorted(
                slots_for_day, key=lambda t: (t != preferred_start if preferred_start else False, t)
            )

            for slot_start in ordered_slots:
                key = slot_start.strftime("%H:%M")
                day_grid = grid.setdefault(day, {})
                if day_grid.get(key) is None and _slot_free(grid, day, slot_start, slot_minutes):
                    day_grid[key] = subject.subject
                    placed += 1
                    break

        if placed < placements_needed:
            unscheduled.append(
                {
                    "subject": subject.subject,
                    "requested": placements_needed,
                    "placed": placed,
                }
            )

    return {"grid": grid, "unscheduled": unscheduled}


def _slot_free(grid: dict, day: str, slot_start: time, duration_minutes: int) -> bool:
    """Check the requested slot and any additional 30-min sub-slots it spans are unoccupied."""
    day_grid = grid.get(day, {})
    cursor = datetime.combine(date.today(), slot_start)
    end = cursor + timedelta(minutes=duration_minutes)
    while cursor < end:
        key = cursor.time().strftime("%H:%M")
        if day_grid.get(key):
            return False
        cursor += timedelta(minutes=30)
    return True
