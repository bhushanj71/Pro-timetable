"""
Recurring events are materialized as individual Event rows sharing a
recurrence_group_id, rather than expanded on the fly with an RRULE engine.
This keeps conflict detection, editing, and calendar rendering simple.

Supported recurrence_rule formats:
    "weekly:MON,WED,FRI"
    "daily"
    "weekday"                (Mon-Fri)
    "monthly:FIRST:MON"      (first Monday of every month)
"""
from datetime import datetime, timedelta

DAY_CODES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

DEFAULT_HORIZON_WEEKS = 16


def generate_occurrence_starts(
    first_start: datetime,
    recurrence_rule: str | None,
    horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
) -> list[datetime]:
    """Return the list of start datetimes for a recurring event, including the first."""
    if not recurrence_rule:
        return [first_start]

    horizon_end = first_start + timedelta(weeks=horizon_weeks)
    occurrences: list[datetime] = []

    if recurrence_rule == "daily":
        cursor = first_start
        while cursor <= horizon_end:
            occurrences.append(cursor)
            cursor += timedelta(days=1)

    elif recurrence_rule == "weekday":
        cursor = first_start
        while cursor <= horizon_end:
            if cursor.weekday() < 5:
                occurrences.append(cursor)
            cursor += timedelta(days=1)

    elif recurrence_rule.startswith("weekly:"):
        days = recurrence_rule.split(":", 1)[1].split(",")
        day_indices = {DAY_CODES.index(d.strip().upper()) for d in days if d.strip().upper() in DAY_CODES}
        cursor = first_start
        while cursor <= horizon_end:
            if cursor.weekday() in day_indices:
                occurrences.append(cursor)
            cursor += timedelta(days=1)

    elif recurrence_rule.startswith("monthly:"):
        # monthly:FIRST:MON  -> first Monday of every month
        parts = recurrence_rule.split(":")
        ordinal = parts[1].upper() if len(parts) > 1 else "FIRST"
        target_day = DAY_CODES.index(parts[2].upper()) if len(parts) > 2 else first_start.weekday()
        ordinal_map = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4}
        n = ordinal_map.get(ordinal, 1)

        cursor_month = first_start.replace(day=1)
        months_ahead = 0
        while months_ahead < (horizon_weeks // 4 + 1):
            candidates = []
            day = cursor_month
            while day.month == cursor_month.month:
                if day.weekday() == target_day:
                    candidates.append(day)
                day += timedelta(days=1)
            if len(candidates) >= n:
                occ = candidates[n - 1].replace(
                    hour=first_start.hour, minute=first_start.minute, second=0, microsecond=0
                )
                if occ >= first_start:
                    occurrences.append(occ)
            # advance to next month
            if cursor_month.month == 12:
                cursor_month = cursor_month.replace(year=cursor_month.year + 1, month=1)
            else:
                cursor_month = cursor_month.replace(month=cursor_month.month + 1)
            months_ahead += 1

    else:
        occurrences = [first_start]

    return occurrences or [first_start]
