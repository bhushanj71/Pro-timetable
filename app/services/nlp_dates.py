"""
Resolves the date/time fields the AI extracts (which may be relative words
like "tomorrow", a weekday name for recurring events, or an ISO date) into
concrete timezone-aware datetimes in the professor's timezone.

The LLM does the heavy natural-language lifting; this module turns its
output into unambiguous datetimes and also offers a small rule-based
fallback for when no AI provider is configured.
"""
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as dateutil_parser

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def get_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def resolve_date(day_or_date: str | None, tz_name: str, reference: datetime | None = None) -> date:
    """
    Resolve a value like "Monday", "tomorrow", "next Friday", or an ISO
    date string into a concrete date, relative to `reference` (defaults to
    now in tz_name).
    """
    tz = get_tz(tz_name)
    now = reference or datetime.now(tz)
    today = now.date()

    if not day_or_date or day_or_date.strip().lower() in ("null", "none", ""):
        return today

    text = day_or_date.strip().lower()

    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text in ("day after tomorrow", "the day after tomorrow"):
        return today + timedelta(days=2)

    is_next = text.startswith("next ")
    bare = text.replace("next ", "").replace("this ", "").strip()

    if bare in WEEKDAYS:
        target_idx = WEEKDAYS.index(bare)
        current_idx = today.weekday()
        delta = (target_idx - current_idx) % 7
        if delta == 0 and is_next:
            delta = 7
        elif delta == 0 and not is_next:
            delta = 0  # "this Monday" on a Monday means today
        if is_next and delta < 7 and delta == 0:
            delta = 7
        return today + timedelta(days=delta)

    # Try ISO / natural date string as a last resort
    try:
        parsed = dateutil_parser.parse(day_or_date, default=datetime.combine(today, time.min))
        return parsed.date()
    except (ValueError, OverflowError):
        return today


def resolve_time(time_str: str | None, default: time = time(9, 0)) -> time:
    # LLMs sometimes emit the literal strings "null"/"none" instead of JSON null.
    if not time_str or time_str.strip().lower() in ("null", "none", ""):
        return default
    text = time_str.strip()
    try:
        if re.match(r"^\d{1,2}:\d{2}$", text):
            h, m = text.split(":")
            return time(int(h), int(m))
        parsed = dateutil_parser.parse(text)
        return parsed.time()
    except (ValueError, OverflowError):
        return default


def combine(d: date, t: time, tz_name: str) -> datetime:
    """
    Combine a date/time in the professor's timezone into a UTC-aware
    datetime. Events are always stored as UTC so that ordering/filtering
    stays correct across both SQLite (which drops tzinfo on read-back) and
    Postgres (which preserves it) — mixing offsets would otherwise break
    chronological string comparison on SQLite.
    """
    local_dt = datetime.combine(d, t, tzinfo=get_tz(tz_name))
    return local_dt.astimezone(timezone.utc)


def ensure_aware_utc(dt: datetime) -> datetime:
    """Normalize a datetime that may have lost its tzinfo on a SQLite round-trip.
    Every datetime written to the DB is already UTC, so a naive value is
    assumed to already represent UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def weekday_code(d: date) -> str:
    return ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][d.weekday()]
