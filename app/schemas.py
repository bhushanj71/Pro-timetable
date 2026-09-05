"""
Pydantic schemas: request/response validation and the AI structured-output
contracts. AI-generated JSON is always parsed into these models before it
touches the database — never executed directly.
"""
from datetime import date, datetime, timezone
from typing import Literal, Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_serializer, field_validator, model_validator


class UTCModel(BaseModel):
    """Base for responses containing datetimes.

    Every datetime is stored as UTC, but SQLite drops tzinfo on read-back, so
    the value would serialize as a bare "2026-08-23T09:48:49". JavaScript
    parses a string with no offset as *local* time, shifting every displayed
    time by the user's UTC offset. Stamping the offset makes the wire format
    unambiguous and identical across SQLite and Postgres.
    """

    @field_serializer("*", when_used="json")
    def _serialize_utc(self, value: Any) -> Any:
        if isinstance(value, datetime):
            aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return aware.astimezone(timezone.utc).isoformat()
        return value


# ---------------------------------------------------------------------------
# Auth / Users
# ---------------------------------------------------------------------------

HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    timezone: str = "Asia/Kolkata"

    # Checked on the form and again here. The box is what somebody ticks; this
    # is what makes the account impossible to create without it, including by
    # anyone posting to the endpoint directly. Literal[True], not bool: false
    # has to be a validation error rather than a quietly unrecorded agreement.
    accepted_terms: Literal[True]

    # College timings vary per professor (08:45-16:15, 09:45-17:15, 11:00-18:30,
    # ...), and they drive the timetable grid, the generator, and free-time
    # search — so they are collected at signup rather than assumed.
    working_hours_start: str = Field(default="09:00", pattern=HHMM_PATTERN)
    working_hours_end: str = Field(default="17:00", pattern=HHMM_PATTERN)
    lunch_start: str = Field(default="13:00", pattern=HHMM_PATTERN)
    lunch_end: str = Field(default="13:30", pattern=HHMM_PATTERN)
    working_days: str = "Mon,Tue,Wed,Thu,Fri"

    @field_validator("working_hours_end")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("working_hours_start")
        if start and v <= start:
            raise ValueError("working_hours_end must be after working_hours_start")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    timezone: str
    department: Optional[str] = None
    designation: Optional[str] = None
    college: Optional[str] = None
    working_days: str
    working_hours_start: str
    working_hours_end: str
    lunch_start: str
    lunch_end: str
    default_lecture_duration: int
    default_reminder_minutes: int
    preferred_ai_provider: Optional[str] = None
    is_admin: bool = False
    notify_email: bool = True
    notify_push: bool = True
    google_sync_enabled: bool = False
    avatar_url: Optional[str] = None
    onboarding_completed: bool = False

    # Planning constraints, so the profile form can render what is already set
    # rather than showing everyone the defaults.
    day_start: str = "07:00"
    day_end: str = "22:30"
    dinner_start: str = "20:00"
    dinner_end: str = "20:45"
    exercise_minutes: int = 45
    exercise_when: str = "morning"
    commute_minutes: int = 0
    study_block_min: int = 45
    study_block_max: int = 120
    study_target_minutes: int = 180
    break_minutes: int = 15
    focus_period: str = "morning"
    subject_priorities: Optional[str] = None
    semester_start: Optional[date] = None

    model_config = {"from_attributes": True}


class AdminUserOut(UTCModel):
    """Richer user view, admin-only. Never exposes password_hash."""

    id: str
    name: str
    email: str
    department: Optional[str] = None
    designation: Optional[str] = None
    college: Optional[str] = None
    timezone: str
    is_admin: bool
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None
    event_count: int = 0
    task_count: int = 0
    # Set when this account administers a college. Two fields because the
    # panel needs to name the college and the id is what it acts on.
    # The college they belong to, by id. The panel needs it to appoint them:
    # an administrator runs the college they are a member of, so the id it
    # sends has to be this one.
    college_id: Optional[str] = None
    admin_college_id: Optional[str] = None
    admin_college: Optional[str] = None
    # Whether the administrator who asked may change this row. The server
    # enforces it regardless; this only keeps the panel from offering buttons
    # that are going to be refused.
    manageable: bool = True

    model_config = {"from_attributes": True}


class AdminUserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    college: Optional[str] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class AdminCreateUser(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    is_admin: bool = False
    department: Optional[str] = None
    designation: Optional[str] = None


class AdminStats(BaseModel):
    total_users: int
    active_users: int
    admin_users: int
    total_events: int
    total_tasks: int
    total_reminders: int
    pending_reminders: int
    ai_conversations: int
    new_users_this_week: int


class UserProfileUpdate(BaseModel):
    name: Optional[str] = None
    notify_email: Optional[bool] = None
    notify_push: Optional[bool] = None
    timezone: Optional[str] = None
    # college and department are deliberately not settable here any more. They
    # are ids now, and a department is only valid inside its own college --
    # a check this endpoint cannot make from two loose strings. Writing them
    # here would let user.college say one thing while college_id said another.
    # PUT /api/org/profile owns that pair.
    designation: Optional[str] = None
    working_days: Optional[str] = None
    working_hours_start: Optional[str] = None
    working_hours_end: Optional[str] = None
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None
    default_lecture_duration: Optional[int] = None
    default_reminder_minutes: Optional[int] = None
    preferred_ai_provider: Optional[str] = None

    # --- Personal planning constraints ---------------------------------
    # Read by the day planner. Bounded here rather than in the planner, so a
    # nonsense value is refused at the edge instead of producing a plan with a
    # nine-hour study block in it.
    day_start: Optional[str] = None
    day_end: Optional[str] = None
    dinner_start: Optional[str] = None
    dinner_end: Optional[str] = None
    exercise_minutes: Optional[int] = Field(default=None, ge=0, le=240)
    exercise_when: Optional[str] = None
    commute_minutes: Optional[int] = Field(default=None, ge=0, le=180)
    study_block_min: Optional[int] = Field(default=None, ge=15, le=240)
    study_block_max: Optional[int] = Field(default=None, ge=15, le=480)
    study_target_minutes: Optional[int] = Field(default=None, ge=0, le=720)
    break_minutes: Optional[int] = Field(default=None, ge=0, le=120)
    focus_period: Optional[str] = None
    subject_priorities: Optional[str] = Field(default=None, max_length=2000)
    semester_start: Optional[date] = None

    @field_validator("exercise_when", "focus_period")
    @classmethod
    def _known_period(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"morning", "afternoon", "evening", "any"}
        if v not in allowed:
            raise ValueError(f"must be one of {sorted(allowed)}")
        return v

    @model_validator(mode="after")
    def _blocks_make_sense(self):
        lo, hi = self.study_block_min, self.study_block_max
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("study_block_min cannot be longer than study_block_max")
        return self


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    event_type: str = "other"
    subject: Optional[str] = None
    start_datetime: datetime
    end_datetime: datetime
    faculty: Optional[str] = Field(default=None, max_length=255)
    location: Optional[str] = Field(default=None, max_length=255)
    location_detail: Optional[str] = None
    location_url: Optional[str] = Field(default=None, max_length=512)
    priority: str = "medium"
    recurrence_rule: Optional[str] = None
    is_all_day: bool = False
    reminder_minutes: Optional[list[int]] = None

    @field_validator("end_datetime")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("start_datetime")
        if start and v <= start:
            raise ValueError("end_datetime must be after start_datetime")
        return v


class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    event_type: Optional[str] = None
    subject: Optional[str] = None
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    faculty: Optional[str] = Field(default=None, max_length=255)
    location: Optional[str] = Field(default=None, max_length=255)
    location_detail: Optional[str] = None
    location_url: Optional[str] = Field(default=None, max_length=512)
    priority: Optional[str] = None
    recurrence_rule: Optional[str] = None
    is_all_day: Optional[bool] = None
    is_cancelled: Optional[bool] = None


class EventReminderCreate(BaseModel):
    """One reminder for an existing event, chosen from the Manage sheet.

    Capped at a week: past that it is a diary entry, not a reminder, and an
    unbounded value would silently create something that fires at a nonsense
    time.
    """

    minutes_before: int = Field(ge=0, le=7 * 24 * 60)


class EventOut(UTCModel):
    id: str
    user_id: str
    title: str
    description: Optional[str] = None
    event_type: str
    subject: Optional[str] = None
    start_datetime: datetime
    end_datetime: datetime
    faculty: Optional[str] = None
    location: Optional[str] = None
    location_detail: Optional[str] = None
    location_url: Optional[str] = None
    priority: str
    recurrence_rule: Optional[str] = None
    recurrence_group_id: Optional[str] = None
    is_all_day: bool
    is_cancelled: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

class ReminderCreate(BaseModel):
    event_id: Optional[str] = None
    task_id: Optional[str] = None
    title: Optional[str] = None
    reminder_datetime: datetime
    reminder_type: str = "in_app"


class ReminderOut(UTCModel):
    id: str
    event_id: Optional[str] = None
    task_id: Optional[str] = None
    title: Optional[str] = None
    reminder_datetime: datetime
    reminder_type: str
    is_sent: bool
    delivery_status: str
    read_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: str = "medium"


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: Optional[str] = None
    status: Optional[str] = None


class TaskOut(UTCModel):
    id: str
    title: str
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: str
    status: str
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# AI structured contracts
# ---------------------------------------------------------------------------

class ScheduleEvent(BaseModel):
    """One event as extracted by the AI. Validated before DB write."""

    title: str
    event_type: str = "other"
    subject: Optional[str] = None
    day: Optional[str] = None  # e.g. "Monday" for recurring weekly events
    date: Optional[str] = None  # ISO date for one-off events, e.g. "2026-08-25"
    # Optional because models frequently omit or null the end time when the
    # user didn't state one; the router derives a sane default duration.
    start_time: Optional[str] = None  # "HH:MM" 24h
    end_time: Optional[str] = None  # "HH:MM" 24h
    recurrence: Optional[str] = None  # "weekly" | "daily" | "monthly" | None
    recurrence_days: Optional[list[str]] = None  # for multi-day weekly recurrence
    faculty: Optional[str] = None
    location: Optional[str] = None
    location_detail: Optional[str] = None
    location_url: Optional[str] = None
    priority: str = "medium"
    reminder_minutes: Optional[int] = None
    description: Optional[str] = None

    @field_validator("recurrence_days", mode="before")
    @classmethod
    def _null_list(cls, v):
        """The literal string "null" fails list validation and discards the
        whole extraction, dropping a good request to the fallback parser."""
        if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
            return None
        return v

    @field_validator("reminder_minutes", mode="before")
    @classmethod
    def _null_int(cls, v):
        if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
            return None
        return v

    # LLMs routinely emit the literal string "null" instead of JSON null.
    @field_validator(
        "subject", "day", "date", "start_time", "end_time", "recurrence",
        "faculty", "location", "location_detail", "location_url", "description",
        mode="before",
    )
    @classmethod
    def _normalize_null_strings(cls, v):
        if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
            return None
        return v

    # ...and `"priority": null` / `"event_type": null` rather than omitting the
    # key, which would otherwise fail validation and silently drop the whole
    # extraction to the rule-based fallback.
    @field_validator("event_type", "priority", mode="before")
    @classmethod
    def _default_if_null(cls, v, info):
        if v is None or (isinstance(v, str) and v.strip().lower() in ("null", "none", "")):
            return "other" if info.field_name == "event_type" else "medium"
        return v


class AIReminder(BaseModel):
    title: str
    date: Optional[str] = None
    time: Optional[str] = None
    minutes_before_event: Optional[int] = None
    related_event_title: Optional[str] = None


class AITask(BaseModel):
    title: str
    due_date: Optional[str] = None
    priority: str = "medium"

    @field_validator("priority", mode="before")
    @classmethod
    def _default_priority(cls, v):
        return "medium" if v is None else v


class AIExtractionResult(BaseModel):
    """Top-level structured response returned by the AI service."""

    intent: str
    # For UPDATE_EVENT / DELETE_EVENT: which existing event the professor
    # means. Matched case-insensitively against their schedule.
    target_event_title: Optional[str] = None
    # Restricts the match when they name a day ("cancel Friday's lecture").
    target_day: Optional[str] = None
    # UPDATE_EVENT only: where the event should move to. Any left unset keeps
    # the current value.
    new_date: Optional[str] = None
    new_day: Optional[str] = None
    new_start_time: Optional[str] = None
    new_end_time: Optional[str] = None
    apply_to_series: bool = False
    # UPDATE_EVENT: field-level replacements, so "change the room" leaves the
    # time alone and vice versa.
    new_faculty: Optional[str] = None
    new_location: Optional[str] = None
    # Reminder-management intents.
    reminder_minutes_before: Optional[int] = None
    reminder_scope: Optional[str] = None
    # CANCEL_DAY: which day is off, and why.
    holiday_date: Optional[str] = None
    holiday_reason: Optional[str] = None

    @field_validator(
        "target_event_title", "target_day", "new_date", "new_day",
        "new_start_time", "new_end_time", "new_faculty", "new_location",
        "reminder_scope", "query_text", "target_date",
        "holiday_date", "holiday_reason",
        mode="before",
    )
    @classmethod
    def _blank_strings_to_none(cls, v):
        """The literal string "null" is truthy, so without this an update
        wrote "null" into the faculty and location columns as though the
        professor had typed it."""
        if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
            return None
        return v

    @field_validator("reminder_minutes_before", "duration_minutes", mode="before")
    @classmethod
    def _blank_ints_to_none(cls, v):
        """LLMs emit the string "null" for integers as readily as for strings,
        and a ValidationError here discards the whole extraction and silently
        drops the request to the rule-based fallback."""
        if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
            return None
        return v
    events: list[ScheduleEvent] = Field(default_factory=list)
    reminders: list[AIReminder] = Field(default_factory=list)
    tasks: list[AITask] = Field(default_factory=list)
    query_text: Optional[str] = None  # for QUERY_SCHEDULE / FIND_FREE_TIME
    duration_minutes: Optional[int] = None  # for FIND_FREE_TIME
    target_date: Optional[str] = None  # for FIND_FREE_TIME / QUERY_SCHEDULE
    notes: Optional[str] = None  # human-readable explanation from the AI


class AIPromptRequest(BaseModel):
    # Which mode the command was typed in. The router refuses to let a Work
    # command touch personal data and vice versa, so this is a boundary, not
    # a hint.
    profile: Optional[str] = None

    prompt: str = Field(min_length=1, max_length=2000)


class AIPromptResponse(BaseModel):
    intent: str
    extraction: AIExtractionResult
    summary: str  # human-readable confirmation text for the UI
    conflicts: list[dict] = Field(default_factory=list)
    requires_confirmation: bool = True
    # Existing events an update/delete would affect, so the professor can see
    # exactly what is about to change before confirming.
    matches: list[dict] = Field(default_factory=list)
    action: str = "create"  # create | update | delete
    # Whether a spoken command may be applied without the professor seeing it
    # first. Decided here rather than in the browser so one rule governs every
    # client, and so a new intent cannot quietly inherit "just do it".
    auto_apply: bool = False


class AIConfirmRequest(BaseModel):
    extraction: AIExtractionResult


# ---------------------------------------------------------------------------
# Timetable generator
# ---------------------------------------------------------------------------

class SubjectRequirement(BaseModel):
    subject: str
    lectures_per_week: int
    duration_minutes: int = 60
    event_type: str = "lecture"
    preferred_days: Optional[list[str]] = None
    preferred_start_time: Optional[str] = None


class TimetableGenerateRequest(BaseModel):
    subjects: list[SubjectRequirement]
    working_days: list[str] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    working_hours_start: str = "09:00"
    working_hours_end: str = "17:00"
    lunch_start: Optional[str] = "13:00"
    lunch_end: Optional[str] = "13:30"
    slot_minutes: int = 60
    avoid_after: Optional[str] = None  # e.g. "16:00"


class FreeTimeRequest(BaseModel):
    date: Optional[str] = None  # defaults to today
    duration_minutes: int = 60
