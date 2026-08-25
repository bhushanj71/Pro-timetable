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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    # Null for accounts created via Google Sign-In, which have no local password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
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

    # Notification preferences. Reminders are delivered to whichever channels
    # are enabled; in-app is always available in the notification centre.
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_push: Mapped[bool] = mapped_column(Boolean, default=True)
    # Secret path segment for the read-only ICS feed, so a calendar app can
    # subscribe without cookies. Rotatable from the profile page.
    calendar_token: Mapped[str] = mapped_column(String(64), default=lambda: uuid.uuid4().hex, index=True)

    # --- Google account linkage ---
    google_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Encrypted at rest; grants offline access so events can be pushed to the
    # professor's Google Calendar without them being present.
    google_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Which mode to open in. One account, two views -- never two accounts.
    active_profile: Mapped[str] = mapped_column(String(16), default="personal")

    # Work notification categories. Task *assignments* are deliberately not
    # listed: a professor who silenced those would stop seeing work other
    # people are waiting on them for, which is not a preference, it is a
    # broken obligation. Everything else is optional.
    notify_work_responses: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_work_progress: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_work_completion: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_work_deadlines: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_work_community: Mapped[bool] = mapped_column(Boolean, default=True)

    # First-run notification setup. Stored server-side so the prompt follows
    # the professor to any browser until they've actually made a choice.
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list["Event"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    conversations: Mapped[list["AIConversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        # "my events between these dates" is what the dashboard, timetable,
        # calendar, next-class and conflict queries all ask. Ordered
        # user_id first: it is the equality half of the predicate, so it
        # narrows before the range scan.
        Index("ix_events_user_start", "user_id", "start_datetime"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), default=EventType.OTHER.value)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # Who is taking it. Not the account holder: a professor's timetable also
    # records classes they are not personally delivering.
    faculty: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # `location` stays the short label shown on a card ("Room 302"). The two
    # below carry the detail a map or a direction needs, kept separate so a
    # card never has to render a URL.
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    priority: Mapped[str] = mapped_column(String(16), default=Priority.MEDIUM.value)

    # e.g. "weekly:MON,WED,FRI" or "daily" or "monthly:1" or None for one-off
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recurrence_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    is_all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Id of the mirrored event in the professor's Google Calendar, so edits
    # and deletions here propagate rather than creating duplicates.
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped["User"] = relationship(back_populates="events")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        # The delivery sweep asks for unsent reminders that are due, and the
        # bell asks the same question scoped to one user.
        Index("ix_reminders_due", "is_sent", "reminder_datetime"),
        Index("ix_reminders_user_due", "user_id", "reminder_datetime"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("events.id"), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reminder_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reminder_type: Mapped[str] = mapped_column(String(16), default=ReminderType.IN_APP.value)

    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Separate from is_sent: delivered is not the same as seen, and the bell
    # badge must clear once the professor has actually looked at it.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cleared from the bell by the professor. Distinct from read_at so the
    # Reminders page keeps the full history after the feed is tidied up.
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class PushSubscription(Base):
    """A browser/device Web Push endpoint.

    One professor can have several (phone, laptop, tablet), so reminders fan
    out to every registered device. Endpoints expire or get revoked, at which
    point the push service returns 404/410 and we delete the row.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="push_subscriptions")


class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    prompt: Mapped[str] = mapped_column(Text)
    ai_response: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="conversations")


# ===========================================================================
# Work mode
#
# Personal data (Event, Task, Reminder above) is untouched. Work data lives in
# its own tables keyed by community, so the two modes cannot leak into each
# other by construction rather than by remembering to filter -- one account,
# two disjoint sets of rows.
# ===========================================================================


class CommunityRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class InviteStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REVOKED = "revoked"


class WorkTaskStatus(str, enum.Enum):
    """Lifecycle of the task as a whole."""

    DRAFT = "draft"
    PENDING_ACCEPTANCE = "pending_acceptance"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AssignmentStatus(str, enum.Enum):
    """Lifecycle of one person's share of a task.

    Deliberately separate from the task's own status: a task can be active
    while one member has accepted, another is still deciding, and a third has
    declined. Collapsing these into one field is what makes "assigned" quietly
    mean "obligated" without the person ever agreeing.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DECLINED = "declined"


class Community(Base):
    __tablename__ = "communities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # An emoji rather than an upload: no storage, no moderation, renders
    # everywhere, and it is enough to tell three communities apart at a glance.
    icon: Mapped[str] = mapped_column(String(16), default="\U0001F465")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    members: Mapped[list["CommunityMember"]] = relationship(
        back_populates="community", cascade="all, delete-orphan"
    )
    invitations: Mapped[list["CommunityInvitation"]] = relationship(
        back_populates="community", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["WorkTask"]] = relationship(
        back_populates="community", cascade="all, delete-orphan"
    )


class CommunityMember(Base):
    __tablename__ = "community_members"
    __table_args__ = (
        # One membership per person per community, enforced by the database
        # rather than by a check that a race can slip past.
        UniqueConstraint("community_id", "user_id", name="uq_community_member"),
        Index("ix_member_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    community_id: Mapped[str] = mapped_column(String(36), ForeignKey("communities.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default=CommunityRole.MEMBER.value)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    community: Mapped["Community"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()


class CommunityInvitation(Base):
    __tablename__ = "community_invitations"
    __table_args__ = (Index("ix_invite_invitee", "invitee_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    community_id: Mapped[str] = mapped_column(String(36), ForeignKey("communities.id"), index=True)
    inviter_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    invitee_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=InviteStatus.PENDING.value)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    community: Mapped["Community"] = relationship(back_populates="invitations")
    inviter: Mapped["User"] = relationship(foreign_keys=[inviter_id])
    invitee: Mapped["User"] = relationship(foreign_keys=[invitee_id])


class WorkTask(Base):
    __tablename__ = "work_tasks"
    __table_args__ = (Index("ix_worktask_community_status", "community_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    community_id: Mapped[str] = mapped_column(String(36), ForeignKey("communities.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(24), default=WorkTaskStatus.PENDING_ACCEPTANCE.value)

    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    community: Mapped["Community"] = relationship(back_populates="tasks")
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    assignments: Mapped[list["TaskAssignment"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    comments: Mapped[list["TaskComment"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskAssignment(Base):
    """One person's share of a task.

    A task is never "assigned" to someone in the sense of being their
    responsibility until they accept it: this row starts PENDING and only
    reaches the assignee's active list once they say yes.
    """

    __tablename__ = "task_assignments"
    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_task_assignee"),
        Index("ix_assignment_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_tasks.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    status: Mapped[str] = mapped_column(String(16), default=AssignmentStatus.PENDING.value)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["WorkTask"] = relationship(back_populates="assignments")
    user: Mapped["User"] = relationship()
    updates: Mapped[list["TaskProgressUpdate"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class TaskProgressUpdate(Base):
    """An entry in the timeline of one person's work on a task."""

    __tablename__ = "task_progress_updates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assignment_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_assignments.id"), index=True)
    from_progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(24), default="progress")  # progress|status|note
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    assignment: Mapped["TaskAssignment"] = relationship(back_populates="updates")


class TaskComment(Base):
    __tablename__ = "task_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_tasks.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    task: Mapped["WorkTask"] = relationship(back_populates="comments")
    user: Mapped["User"] = relationship()


class WorkNotification(Base):
    """Work-mode notification feed.

    Kept apart from Reminder rather than folded into it. Reminder drives the
    personal schedule's push and email delivery; mixing "Rahul accepted your
    task" into it would put work content in the personal bell and break the
    isolation this feature exists to provide.
    """

    __tablename__ = "work_notifications"
    __table_args__ = (Index("ix_worknotif_user_read", "user_id", "read_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    # Who caused it. Needed to render "Rahul accepted…" with an avatar, and to
    # suppress notifying someone about their own action.
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    community_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assignment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Identity of a recurring notification, e.g. "due-soon for task X".
    # Deadline and chase-up reminders are generated by a sweep that runs every
    # few minutes; without a key to check against, each pass would send the
    # same "due tomorrow" again.
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship()
