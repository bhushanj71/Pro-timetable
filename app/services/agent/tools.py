"""What the agent is allowed to do.

Every tool is a thin wrapper over something the application already does. The
scheduling, the conflict detection and the free-slot search were all written
before any of this and are not reimplemented here -- an agent that computed its
own availability would be a second answer to a question that already has one,
and the two would disagree the first time either changed.

Three rules hold for every tool in this registry, and they are the reason it is
a registry rather than a set of functions the model can name:

  * A tool takes no user id. The signed-in user is passed by the runner from
    the request, and no argument the model produces can redirect it. There is
    therefore no argument a model could invent that reaches another person's
    rows -- not a mistake it is unlikely to make, one it cannot express.

  * Arguments are validated against a declared schema before the handler is
    entered, so a handler never defends itself against a hallucinated type.

  * Only names in this registry can be called at all. A model asking for
    `run_sql` gets an error naming the tools that exist.

Read-only tools are marked as such. The runner uses that to decide what may
run unattended and what has to be put to the user first, so the distinction
lives with the tool rather than in a list somewhere else that can drift.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import Task, TaskStatus, User
from app.services import schedule_query as sq
from app.services import scheduler
from app.services.nlp_dates import ensure_aware_utc

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """A tool refusing its arguments, in words the model can act on."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict            # JSON Schema for the arguments
    handler: Callable[..., Any]
    read_only: bool = True
    # What the professor is told while this runs. Written here rather than
    # asked of the model: the tool already knows what it does, a model asked
    # to narrate it is one more field it can get wrong, and a status line it
    # composes is a place its own reasoning can leak onto the screen.
    status: str = "Working"

    def run(self, db: Session, user: User, args: dict) -> Any:
        return self.handler(db, user, **_validated(self, args))


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, parameters: dict, *,
         read_only: bool = True, status: str = "Working"):
    def wrap(fn):
        REGISTRY[name] = Tool(name, description, parameters, fn, read_only, status)
        return fn
    return wrap


# ---------------------------------------------------------------------------
# Argument checking
# ---------------------------------------------------------------------------
def _validated(t: Tool, args: dict) -> dict:
    """Check arguments against the tool's schema.

    Deliberately small: this validates the shapes these tools actually declare
    rather than all of JSON Schema. A dependency that understands every keyword
    would still have to be told which keywords we use, and the failure it would
    prevent -- a tool declaring something this cannot check -- is caught by the
    test that walks the registry.

    Unknown keys are refused rather than dropped. A model that invents
    `user_id` is trying to reach somebody else's rows, and silently ignoring it
    would hide that from the log.
    """
    props = t.parameters.get("properties", {})
    required = set(t.parameters.get("required", []))
    args = args or {}

    unknown = set(args) - set(props)
    if unknown:
        raise ToolError(f"{t.name} has no argument {', '.join(sorted(unknown))}")

    missing = required - set(args)
    if missing:
        raise ToolError(f"{t.name} needs {', '.join(sorted(missing))}")

    out: dict[str, Any] = {}
    for key, value in args.items():
        spec = props[key]
        kind = spec.get("type")
        if value is None:
            out[key] = None
            continue
        if kind == "integer":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ToolError(f"{t.name}.{key} must be a whole number")
            lo, hi = spec.get("minimum"), spec.get("maximum")
            if lo is not None and value < lo:
                raise ToolError(f"{t.name}.{key} must be at least {lo}")
            if hi is not None and value > hi:
                raise ToolError(f"{t.name}.{key} must be at most {hi}")
        elif kind == "string":
            value = str(value)
            allowed = spec.get("enum")
            if allowed and value not in allowed:
                raise ToolError(f"{t.name}.{key} must be one of {', '.join(allowed)}")
            if spec.get("format") == "date":
                value = _as_date(t.name, key, value).isoformat()
        elif kind == "boolean":
            value = bool(value)
        out[key] = value
    return out


def _as_date(tool_name: str, key: str, value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise ToolError(f"{tool_name}.{key} must be a date as YYYY-MM-DD, not {value!r}")


def _prefs(user: User) -> dict:
    return {
        "timezone": user.timezone,
        "working_days": (user.working_days or "").split(","),
        "working_hours": {"start": user.working_hours_start, "end": user.working_hours_end},
        "lunch": {"start": user.lunch_start, "end": user.lunch_end},
        "default_lecture_minutes": user.default_lecture_duration,
    }


def _today(user: User) -> date:
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).date()


# ---------------------------------------------------------------------------
# Reading the user's own world
# ---------------------------------------------------------------------------
@tool(
    "get_user_profile",
    "Fuller detail about the professor -- department, designation, college. The "
    "timezone, working days, working hours and lunch window are already given "
    "to you each turn, so do not call this just to read those.",
    {"type": "object", "properties": {}, "required": []},
    status="Reading your preferences",
)
def get_user_profile(db: Session, user: User) -> dict:
    return {
        "name": user.name,
        "department": user.department,
        "designation": user.designation,
        "college": user.college,
        "today": _today(user).isoformat(),
        "preferences": _prefs(user),
    }


@tool(
    "get_schedule",
    "The professor's events on one day. Use this to see what a day already "
    "holds before proposing anything for it.",
    {
        "type": "object",
        "properties": {
            "day": {"type": "string", "format": "date",
                    "description": "YYYY-MM-DD. Defaults to today."},
            "teaching_only": {"type": "boolean",
                              "description": "Only lectures and labs. Default false."},
        },
        "required": [],
    },
    status="Checking your schedule",
)
def get_schedule(db: Session, user: User, day: str | None = None,
                 teaching_only: bool | None = None) -> dict:
    when = _as_date("get_schedule", "day", day) if day else _today(user)
    events = sq.events_on_day(db, user, when, teaching_only=bool(teaching_only))
    return {
        "day": when.isoformat(),
        "events": [sq.serialize(e, user.timezone) for e in events],
    }


@tool(
    "find_free_slots",
    "Gaps of at least a given length on one day, within the professor's own "
    "working hours. This is the application's own availability calculation -- "
    "never work out free time yourself.",
    {
        "type": "object",
        "properties": {
            "day": {"type": "string", "format": "date", "description": "YYYY-MM-DD."},
            "duration_minutes": {"type": "integer", "minimum": 5, "maximum": 600},
        },
        "required": ["day", "duration_minutes"],
    },
    status="Looking for free time",
)
def find_free_slots(db: Session, user: User, day: str, duration_minutes: int) -> dict:
    when = _as_date("find_free_slots", "day", day)
    start = _hhmm(user.working_hours_start, time(9, 0))
    end = _hhmm(user.working_hours_end, time(17, 0))
    slots = scheduler.find_free_slots(
        db, user.id, when, duration_minutes, user.timezone, day_start=start, day_end=end
    )
    return {
        "day": when.isoformat(),
        "duration_minutes": duration_minutes,
        # Local wall-clock as well as the instant. Handed only ISO strings with
        # an offset, the model did the conversion itself and reported a slot
        # outside the professor's own working hours -- time arithmetic is
        # exactly what it should not be doing, so it is done here instead.
        "slots": [
            {
                "start": s.isoformat(), "end": e.isoformat(),
                "local": f"{_local_hhmm(s, user)}-{_local_hhmm(e, user)}",
            }
            for s, e in slots
        ],
    }


def _local_hhmm(moment: datetime, user: User) -> str:
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        tz = timezone.utc
    return ensure_aware_utc(moment).astimezone(tz).strftime("%H:%M")


def _hhmm(value: str | None, fallback: time) -> time:
    try:
        hh, mm = (value or "").split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return fallback


@tool(
    "check_schedule_conflicts",
    "Events that overlap each other in the days ahead. Use before promising a "
    "day is clear.",
    {
        "type": "object",
        "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 60}},
        "required": [],
    },
    status="Checking for clashes",
)
def check_schedule_conflicts(db: Session, user: User, days: int | None = None) -> dict:
    return {"conflicts": sq.find_conflicts(db, user, days=days or 7)}


@tool(
    "get_tasks",
    "The professor's own tasks, newest deadline first. Filter by status to ask "
    "only about what is outstanding.",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": [],
    },
    status="Reading your tasks",
)
def get_tasks(db: Session, user: User, status: str | None = None,
              limit: int | None = None) -> dict:
    q = db.query(Task).filter(Task.user_id == user.id)
    if status:
        q = q.filter(Task.status == status)
    rows = q.order_by(Task.due_date.is_(None), Task.due_date).limit(limit or 25).all()
    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "due_date": ensure_aware_utc(t.due_date).isoformat() if t.due_date else None,
                "overdue": bool(
                    t.due_date
                    and ensure_aware_utc(t.due_date) < datetime.now(timezone.utc)
                    and t.status != TaskStatus.COMPLETED.value
                ),
            }
            for t in rows
        ]
    }


@tool(
    "get_next_class",
    "The professor's next teaching commitment, if there is one.",
    {"type": "object", "properties": {}, "required": []},
    status="Finding your next class",
)
def get_next_class(db: Session, user: User) -> dict:
    event = sq.next_class(db, user)
    if not event:
        return {"next_class": None}
    return {"next_class": sq.serialize(event, user.timezone),
            "when": sq.describe_when(event, user.timezone)}


@tool(
    "get_reminders",
    "Reminders the professor has set that have not yet fired.",
    {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        "required": [],
    },
    status="Checking your reminders",
)
def get_reminders(db: Session, user: User, limit: int | None = None) -> dict:
    return {"reminders": sq.active_reminders(db, user, limit=limit or 20)}


# ---------------------------------------------------------------------------
# What the model is told it may call
# ---------------------------------------------------------------------------
def catalogue(*, read_only_only: bool = False) -> list[dict]:
    """The tool list as the model sees it: names, descriptions, argument shapes.

    Descriptions are written for the model, not for us. A tool it cannot tell
    apart from another is a tool it will call at the wrong moment.
    """
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in REGISTRY.values()
        if not read_only_only or t.read_only
    ]


def get(name: str) -> Tool:
    try:
        return REGISTRY[name]
    except KeyError:
        raise ToolError(
            f"There is no tool called {name!r}. Available: {', '.join(sorted(REGISTRY))}"
        )
