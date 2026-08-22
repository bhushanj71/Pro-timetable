"""
SQLAlchemy ORM models: User, Event, Reminder, Task, AIConversation.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, enum.Enum):
    LECTURE = "lecture"
    LAB = "lab"
    MEETING = "meeting"
    PROJECT_REVIEW = "project_review"
    EXAMINATION = "examination"
    PERSONAL = "personal"
    RESEARCH = "research"
    DEADLINE = "deadline"
    CONFERENCE = "conference"
    FDP = "fdp"
    WORKSHOP = "workshop"
    OTHER = "other"


class Priority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReminderType(str, enum.Enum):
    IN_APP = "in_app"
    BROWSER = "browser"
    EMAIL = "email"
    PUSH = "push"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")

    # Profile
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    college: Mapped[str | None] = mapped_column(String(255), nullable=True)
    working_days: Mapped[str] = mapped_column(String(64), default="Mon,Tue,Wed,Thu,Fri")
    working_hours_start: Mapped[str] = mapped_column(String(8), default="09:00")
    working_hours_end: Mapped[str] = mapped_column(String(8), default="17:00")
    lunch_start: Mapped[str] = mapped_column(String(8), default="13:00")
    lunch_end: Mapped[str] = mapped_column(String(8), default="13:30")
    default_lecture_duration: Mapped[int] = mapped_column(Integer, default=60)
    default_reminder_minutes: Mapped[int] = mapped_column(Integer, default=30)
    preferred_ai_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Admin users can manage all accounts via /admin; regular professors can
    # only ever see their own data.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list["Event"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    conversations: Mapped[list["AIConversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), default=EventType.OTHER.value)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.MEDIUM.value)

    # e.g. "weekly:MON,WED,FRI" or "daily" or "monthly:1" or None for one-off
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recurrence_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    is_all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped["User"] = relationship(back_populates="events")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("events.id"), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reminder_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reminder_type: Mapped[str] = mapped_column(String(16), default=ReminderType.IN_APP.value)

    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING.value)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="reminders")
    event: Mapped["Event | None"] = relationship(back_populates="reminders")
    task: Mapped["Task | None"] = relationship(back_populates="reminders")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.MEDIUM.value)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.PENDING.value)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="tasks")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    prompt: Mapped[str] = mapped_column(Text)
    ai_response: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="conversations")
