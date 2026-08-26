"""
The AI natural-language endpoints: prompt processing, confirmation
(actual DB writes happen only here), timetable generation, free-time
finding, conflict resolution, and schedule queries.

Flow: prompt -> AIService -> AIExtractionResult -> conflict check ->
show the professor what was understood -> POST /confirm persists it.
"""
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import AIConversation, Event, Task, User
from app.schemas import (
    AIConfirmRequest,
    AIExtractionResult,
    AIPromptRequest,
    AIPromptResponse,
    FreeTimeRequest,
    ScheduleEvent,
    TimetableGenerateRequest,
)
from app.services.ai_service import get_ai_service
from app.services.conflict_service import find_conflicts
from app.services.nlp_dates import (
    WEEKDAYS,
    combine,
    ensure_aware_utc,
    get_tz,
    resolve_date,
    resolve_time,
    weekday_code,
)
from app.services.ai_guard import OUT_OF_SCOPE, check_prompt
from app.services import activity, schedule_query as sq
from app.services.recurrence import generate_occurrence_starts
from app.services.reminder_service import (
    clear_event_reminders,
    create_reminder_for_event,
    resync_event_reminders,
    schedule_event_reminders,
    schedule_task_reminders,
    set_reminder_lead,
)
from app.services.scheduler import find_free_slots, generate_timetable

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _schedule_event_to_datetimes(evt: ScheduleEvent, tz_name: str) -> tuple[datetime, datetime, str | None]:
    """Resolve a ScheduleEvent's day/date + start/end times into concrete datetimes and a recurrence_rule."""
    day_source = evt.date or evt.day
    recurrence_rule = None

    # Models often get relative-date arithmetic wrong ("next Friday" -> a
    # Wednesday's date) while naming the weekday correctly. When the explicit
    # date contradicts the stated weekday, trust the weekday name.
    if evt.date and evt.day:
        stated_day = evt.day.strip().lower()
        if stated_day in WEEKDAYS:
            resolved_from_date = resolve_date(evt.date, tz_name)
            if resolved_from_date.strftime("%A").lower() != stated_day:
                day_source = evt.day

    if evt.recurrence == "weekly" and evt.recurrence_days:
        first_day = evt.recurrence_days[0]
        base_date = resolve_date(first_day, tz_name)
        codes = []
        for d in evt.recurrence_days:
            resolved = resolve_date(d, tz_name)
            codes.append(weekday_code(resolved))
        recurrence_rule = f"weekly:{','.join(sorted(set(codes)))}"
    elif evt.recurrence == "weekly" and evt.day:
        base_date = resolve_date(evt.day, tz_name)
        recurrence_rule = f"weekly:{weekday_code(base_date)}"
    elif evt.recurrence == "daily":
        base_date = resolve_date(day_source, tz_name)
        recurrence_rule = "daily"
    else:
        base_date = resolve_date(day_source, tz_name)

    start_t = resolve_time(evt.start_time)
    end_t = resolve_time(evt.end_time, default=time((start_t.hour + 1) % 24, start_t.minute))

    start_dt = combine(base_date, start_t, tz_name)
    end_dt = combine(base_date, end_t, tz_name)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)

    return start_dt, end_dt, recurrence_rule



# ---------------------------------------------------------------------------
# What a spoken command is allowed to do on its own
# ---------------------------------------------------------------------------
# Hands-free is worth having, so adding something new to your own schedule
# completes without a tap: it is additive, it is visible the moment it lands,
# and undoing it is one delete.
#
# Everything else is shown first. Speech recognition mishears, and the two
# things it must never do on a mishearing are rewrite something that already
# exists and reach another person. An update is not a smaller delete -- it
# overwrites a real class with whatever was misheard, and there is no copy of
# what was there before.
#
# An allowlist, deliberately, not a list of exclusions. The previous version
# named only delete and cancel_day, so UPDATE_EVENT was applied the instant it
# was spoken, and every intent added afterwards inherited the same silence.
AUTO_APPLY_INTENTS = frozenset({
    "CREATE_EVENT",
    "CREATE_RECURRING_EVENT",
    "CREATE_REMINDER",
})


def _may_auto_apply(intent: str, requires_confirmation: bool) -> bool:
    """True only for additions to the speaker's own schedule."""
    if not requires_confirmation:
        return False          # nothing pending: it was answered or it failed
    return intent in AUTO_APPLY_INTENTS


def _find_target_events(db: Session, user: User, extraction: AIExtractionResult) -> list[Event]:
    """Resolve which existing events an update/delete refers to.

    Prefers upcoming occurrences — "cancel my ANN lecture" almost never means
    one that already happened.
    """
    title = (extraction.target_event_title or "").strip()
    if not title:
        return []

    now = datetime.now(timezone.utc)

    def base_query(phrase: str):
        return db.query(Event).filter(
            Event.user_id == user.id,
            Event.is_cancelled.is_(False),
            Event.title.ilike(f"%{phrase}%"),
        )

    # Natural phrasing carries filler the title doesn't have ("Lab Session
    # classes"). Narrow the phrase until something matches rather than
    # requiring the professor to quote the title exactly.
    words = title.split()
    query = None
    for length in range(len(words), 0, -1):
        candidate = " ".join(words[:length])
        if base_query(candidate).first():
            query = base_query(candidate)
            break
    if query is None:
        return []

    if extraction.target_day:
        target_date = resolve_date(extraction.target_day, user.timezone)
        day_start = combine(target_date, time.min, user.timezone)
        query = query.filter(
            Event.start_datetime >= day_start,
            Event.start_datetime < day_start + timedelta(days=1),
        )
        return query.order_by(Event.start_datetime).all()

    upcoming = query.filter(Event.start_datetime >= now).order_by(Event.start_datetime).all()
    if upcoming:
        # Without an explicit "all", act on the next occurrence only.
        return upcoming if extraction.apply_to_series else upcoming[:1]

    # Nothing ahead: fall back to the most recent match so the professor gets
    # a useful "did you mean this?" rather than silence.
    return query.order_by(Event.start_datetime.desc()).limit(1).all()


def _serialize_match(e: Event, tz_name: str) -> dict:
    # ensure_aware_utc first: SQLite returns naive datetimes, and calling
    # astimezone on those would treat them as machine-local rather than UTC.
    local = ensure_aware_utc(e.start_datetime).astimezone(get_tz(tz_name))
    return {
        "id": e.id,
        "title": e.title,
        "start": e.start_datetime.isoformat(),
        "when": local.strftime("%A, %d %b at %I:%M %p"),
        "location": e.location,
        "is_series": bool(e.recurrence_group_id),
    }


@router.post("/process-prompt", response_model=AIPromptResponse)
def process_prompt(payload: AIPromptRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Refuse before spending a model call. This also holds when no API key is
    # configured and the rule-based parser would otherwise invent an event out
    # of an unrelated sentence.
    # Work mode gets its own parser. Keeping them apart is what guarantees a
    # command in one mode cannot reach the other's data: the code that creates
    # a lecture and the code that assigns a task never see the same request.
    profile = (payload.profile or user.active_profile or "personal").lower()
    if profile == "work":
        answer = handle_work_prompt(db, user, payload.prompt)
        if answer is not None:
            return answer
    else:
        # A work command typed in Personal mode is not refused and not acted
        # on: it is pointed at the right room. Letting the personal parser see
        # it produced nonsense -- "show my active work tasks" came back as a
        # schedule query for a lecture called "string".
        from app.services.work_ai import parse_work_command

        if parse_work_command(payload.prompt):
            return AIPromptResponse(
                intent="WRONG_PROFILE",
                extraction=AIExtractionResult(intent="WRONG_PROFILE"),
                summary="That's a Work command. Switch to 💼 Work and ask again — "
                        "your personal schedule and your team's work are kept separate.",
                requires_confirmation=False,
            )

    allowed, refusal = check_prompt(payload.prompt)
    if not allowed:
        return AIPromptResponse(
            intent=OUT_OF_SCOPE,
            extraction=AIExtractionResult(intent=OUT_OF_SCOPE),
            summary=refusal,
            requires_confirmation=False,
        )

    ai = get_ai_service()
    now = datetime.now(timezone.utc)
    context = {
        "today": date.today().isoformat(),
        "weekday": date.today().strftime("%A"),
        "timezone": user.timezone,
    }
    extraction = ai.process_prompt(payload.prompt, context)

    # The model can also decline, which is the layer that catches phrasings the
    # pattern check above does not know about.
    if extraction.intent == OUT_OF_SCOPE:
        return AIPromptResponse(
            intent=OUT_OF_SCOPE,
            extraction=extraction,
            summary=(
                "I only work on your timetable — lectures, labs, meetings, exams, "
                "deadlines, tasks and reminders. Try naming what and when."
            ),
            requires_confirmation=False,
        )

    # Read-only intents are answered here and now. Routing them through the
    # confirm step would ask the professor to approve a question.
    answered = _answer_read_only(db, user, extraction)
    if answered is not None:
        return answered

    conflicts_out = []
    for evt in extraction.events:
        start_dt, end_dt, _ = _schedule_event_to_datetimes(evt, user.timezone)
        clashes = find_conflicts(db, user.id, start_dt, end_dt)
        if clashes:
            conflicts_out.append(
                {
                    "event_title": evt.title,
                    "conflicts_with": [{"id": c.id, "title": c.title, "start": c.start_datetime.isoformat()} for c in clashes],
                }
            )

    summary_parts = []
    if extraction.notes:
        summary_parts.append(extraction.notes)
    for evt in extraction.events:
        start_dt, end_dt, _ = _schedule_event_to_datetimes(evt, user.timezone)
        local_start = start_dt.astimezone(get_tz(user.timezone))
        local_end = end_dt.astimezone(get_tz(user.timezone))
        summary_parts.append(f"{evt.title}: {local_start.strftime('%A %I:%M %p')} - {local_end.strftime('%I:%M %p')}")
    for r in extraction.reminders:
        summary_parts.append(f"Reminder: {r.title}")
    for t in extraction.tasks:
        summary_parts.append(f"Task: {t.title}")
    summary = " | ".join(summary_parts) if summary_parts else "I couldn't extract anything actionable from that."

    convo = AIConversation(
        user_id=user.id,
        prompt=payload.prompt,
        ai_response=extraction.model_dump_json(),
        intent=extraction.intent,
    )
    db.add(convo)
    db.commit()

    requires_confirmation = extraction.intent not in ("QUERY_SCHEDULE", "FIND_FREE_TIME")

    # Update/delete act on existing events, so resolve them now and describe
    # exactly what would change — never act on a guess without showing it.
    action = "create"
    matches: list[dict] = []
    if extraction.intent in ("DELETE_EVENT", "UPDATE_EVENT"):
        action = "delete" if extraction.intent == "DELETE_EVENT" else "update"
        found = _find_target_events(db, user, extraction)
        matches = [_serialize_match(e, user.timezone) for e in found]

        if not found:
            summary = (
                f"I couldn't find an event matching \"{extraction.target_event_title}\" in your schedule."
                if extraction.target_event_title
                else "I couldn't work out which event you meant."
            )
            requires_confirmation = False
        elif action == "delete":
            summary = f"Delete {len(found)} event(s): " + ", ".join(m["title"] + " — " + m["when"] for m in matches[:3])
        else:
            changes = []
            if extraction.new_day or extraction.new_date:
                changes.append(f"move to {extraction.new_day or extraction.new_date}")
            if extraction.new_start_time:
                changes.append(f"start at {extraction.new_start_time}")
            if extraction.new_end_time:
                changes.append(f"end at {extraction.new_end_time}")
            summary = (
                f"Update {matches[0]['title']} ({matches[0]['when']}): " + ", ".join(changes)
                if changes
                else f"I found {matches[0]['title']} but couldn't tell what to change."
            )
            if not changes:
                requires_confirmation = False

    return AIPromptResponse(
        intent=extraction.intent,
        extraction=extraction,
        summary=summary,
        conflicts=conflicts_out,
        requires_confirmation=requires_confirmation,
        auto_apply=_may_auto_apply(extraction.intent, requires_confirmation),
        matches=matches,
        action=action,
    )


_BLANKET = {"every lecture", "all lectures", "every class", "all classes",
            "every lab", "all labs", "everything", "all", "every event"}


def _is_blanket(scope: str | None) -> bool:
    """Whether a reminder rule applies to the whole schedule rather than one
    subject. "Remind me 15 minutes before every lecture" names no subject, and
    searching for the literal text "every lecture" would match nothing."""
    return (scope or "").strip().lower() in _BLANKET


def handle_work_prompt(db: Session, user: User, prompt: str):
    """Answer a Work-mode command, or return None to fall through.

    Falling through matters: someone in Work mode can still ask "what's my
    next lecture", and that should reach the personal parser rather than being
    refused for being in the wrong room.
    """
    from app.services.work_ai import parse_work_command

    cmd = parse_work_command(prompt)
    if not cmd:
        return None

    from app.services import work_router_actions as actions

    return actions.execute(db, user, cmd)


def _answer_read_only(db: Session, user: User, extraction: AIExtractionResult):
    """Handle the intents that only look things up.

    Returns an AIPromptResponse when it handled the intent, else None so the
    caller falls through to the create/update/delete path.
    """
    intent = extraction.intent

    if intent == "GET_NEXT_CLASS":
        nxt = sq.next_class(db, user)
        if not nxt:
            return AIPromptResponse(
                intent=intent, extraction=extraction, requires_confirmation=False,
                summary="Nothing scheduled in the next two weeks.")
        m = sq.serialize(nxt, user.timezone)
        bits = [m["title"], m["when"]]
        if m["faculty"]:
            bits.append(m["faculty"])
        if m["location"]:
            bits.append(m["location"])
        return AIPromptResponse(
            intent=intent, extraction=extraction, requires_confirmation=False,
            summary="Next up: " + " · ".join(bits), matches=[m], action="view")

    if intent == "SHOW_LOCATION":
        target = None
        if extraction.target_event_title:
            found = _find_target_events(db, user, extraction)
            target = found[0] if found else None
        else:
            # "Where is my class?" means the next one.
            target = sq.next_class(db, user)

        if not target:
            return AIPromptResponse(
                intent=intent, extraction=extraction, requires_confirmation=False,
                summary="I couldn't find that class in your schedule.")

        m = sq.serialize(target, user.timezone)
        if not m["has_location"]:
            return AIPromptResponse(
                intent=intent, extraction=extraction, requires_confirmation=False,
                summary=f"{m['title']} ({m['when']}) has no location saved yet.",
                matches=[m], action="view")
        where = " — ".join(x for x in (m["location"], m["location_detail"]) if x)
        return AIPromptResponse(
            intent=intent, extraction=extraction, requires_confirmation=False,
            summary=f"{m['title']} is at {where} ({m['when']}).",
            matches=[m], action="location")

    if intent == "CANCEL_DAY":
        day = resolve_date(extraction.holiday_date, user.timezone)
        affected = sq.events_on_day(db, user, day)
        pretty = day.strftime("%A, %d %b")

        if not affected:
            return AIPromptResponse(
                intent=intent, extraction=extraction, requires_confirmation=False,
                summary=f"Nothing scheduled on {pretty} \u2014 no classes to cancel.")

        reason = f" ({extraction.holiday_reason})" if extraction.holiday_reason else ""
        return AIPromptResponse(
            intent=intent, extraction=extraction,
            requires_confirmation=True, action="cancel_day",
            summary=(f"Cancel {len(affected)} class{'es' if len(affected) > 1 else ''} "
                     f"on {pretty}{reason}?"),
            matches=[sq.serialize(e, user.timezone) for e in affected])

    if intent == "CHECK_CONFLICTS":
        clashes = sq.find_conflicts(db, user)
        if not clashes:
            return AIPromptResponse(
                intent=intent, extraction=extraction, requires_confirmation=False,
                summary="No clashes in the next seven days.")
        first = clashes[0]
        return AIPromptResponse(
            intent=intent, extraction=extraction, requires_confirmation=False,
            summary=(f"{len(clashes)} clash{'es' if len(clashes) > 1 else ''} this week, "
                     f"starting with {first['a']['title']} and {first['b']['title']} "
                     f"({first['a']['when']})."),
            conflicts=clashes, action="view")

    if intent == "VIEW_REMINDERS":
        rows = sq.active_reminders(db, user)
        if not rows:
            return AIPromptResponse(
                intent=intent, extraction=extraction, requires_confirmation=False,
                summary="You have no reminders waiting.")
        return AIPromptResponse(
            intent=intent, extraction=extraction, requires_confirmation=False,
            summary=f"{len(rows)} reminder{'s' if len(rows) != 1 else ''} coming up.",
            matches=rows[:20], action="view")

    return None


def _reject_out_of_scope(extraction) -> None:
    """A refusal must not be confirmable: the confirm step writes to the
    database, so it re-checks rather than trusting the client's round trip."""
    if getattr(extraction, "intent", None) == OUT_OF_SCOPE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That request is outside what this assistant does")


@router.post("/confirm")
def confirm_extraction(payload: AIConfirmRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    extraction = payload.extraction
    _reject_out_of_scope(extraction)
    created_events, created_reminders, created_tasks = [], [], []
    series_count = 0  # user-facing count: one per distinct weekly slot, not per materialized occurrence

    # Update and delete operate on existing events. Handled first and returned
    # early: previously every intent fell through to the create path, so
    # "cancel my meeting" created a meeting instead of removing one.
    if extraction.intent == "DELETE_EVENT":
        targets = _find_target_events(db, user, extraction)
        if not targets:
            return {"ok": False, "message": "Couldn't find that event.", "deleted": 0}

        # Collect first, de-duplicated: expanding a series can otherwise queue
        # the same row twice. Deleting through the ORM (rather than a bulk
        # query delete) is what cascades to each event's reminders.
        doomed: dict[str, Event] = {}
        for event in targets:
            if extraction.apply_to_series and event.recurrence_group_id:
                for sibling in (
                    db.query(Event)
                    .filter(
                        Event.user_id == user.id,
                        Event.recurrence_group_id == event.recurrence_group_id,
                    )
                    .all()
                ):
                    doomed[sibling.id] = sibling
            else:
                doomed[event.id] = event

        first_title = next(iter(doomed.values())).title
        title, body = activity.summarise_series(len(doomed), f"Deleted {first_title}")
        activity.record(db, user.id, activity.Activity.EVENT_DELETED, title, body)

        for event in doomed.values():
            db.delete(event)
        deleted = len(doomed)
        db.commit()
        return {"ok": True, "action": "deleted", "deleted": deleted}

    if extraction.intent == "UPDATE_EVENT":
        targets = _find_target_events(db, user, extraction)
        if not targets:
            return {"ok": False, "message": "Couldn't find that event.", "updated": 0}

        updated = 0
        last_changes: list[str] = []
        for event in targets:
            start = ensure_aware_utc(event.start_datetime).astimezone(get_tz(user.timezone))
            end = ensure_aware_utc(event.end_datetime).astimezone(get_tz(user.timezone))
            duration = end - start

            new_date = start.date()
            if extraction.new_date or extraction.new_day:
                new_date = resolve_date(extraction.new_date or extraction.new_day, user.timezone)

            new_start_t = resolve_time(extraction.new_start_time, default=start.time())
            new_start = combine(new_date, new_start_t, user.timezone)

            if extraction.new_end_time:
                new_end = combine(new_date, resolve_time(extraction.new_end_time), user.timezone)
                if new_end <= new_start:
                    new_end = new_start + duration
            else:
                new_end = new_start + duration

            changed = []
            if new_start != ensure_aware_utc(event.start_datetime):
                changed.append(
                    f"{start.strftime('%I:%M %p').lstrip('0')} → "
                    f"{new_start.astimezone(get_tz(user.timezone)).strftime('%I:%M %p').lstrip('0')}"
                )
            event.start_datetime = new_start
            event.end_datetime = new_end

            # Field-level updates: changing the room must not disturb the time,
            # and vice versa.
            if extraction.new_faculty:
                changed.append(f"faculty → {extraction.new_faculty}")
                event.faculty = extraction.new_faculty
            if extraction.new_location:
                changed.append(f"location → {extraction.new_location}")
                event.location = extraction.new_location

            # Reminders hang off the old time. Each keeps its own lead rather
            # than being collapsed onto one default.
            resync_event_reminders(db, event)
            updated += 1
            last_changes = changed

        activity.record(db, user.id, activity.Activity.EVENT_UPDATED,
                        f"Updated {targets[0].title}",
                        ", ".join(last_changes) or None)
        db.commit()
        detail = ", ".join(last_changes) if last_changes else "no visible change"
        return {
            "ok": True, "action": "updated", "updated": updated,
            "message": f"Updated {targets[0].title}: {detail}. Reminders rescheduled.",
            "event": sq.serialize(targets[0], user.timezone),
        }

    if extraction.intent == "CANCEL_DAY":
        day = resolve_date(extraction.holiday_date, user.timezone)
        affected = sq.events_on_day(db, user, day)
        if not affected:
            return {"ok": False, "message": "Nothing scheduled that day.", "cancelled": 0}

        # Marked cancelled rather than deleted. A holiday is frequently
        # revised -- the festival moves, the strike is called off -- and the
        # professor should get the day back without retyping a timetable.
        for event in affected:
            event.is_cancelled = True
            clear_event_reminders(db, event)

        activity.record(db, user.id, activity.Activity.EVENT_CANCELLED,
                        f"Cancelled {len(affected)} class(es)",
                        day.strftime("%A, %d %b"))
        db.commit()
        pretty = day.strftime("%A, %d %b")
        return {
            "ok": True, "action": "cancelled_day", "cancelled": len(affected),
            "message": (f"{len(affected)} class{'es' if len(affected) > 1 else ''} cancelled "
                        f"on {pretty}. Their reminders are off too."),
        }

    if extraction.intent in ("UPDATE_REMINDER", "DELETE_REMINDER") and (
        extraction.reminder_scope or extraction.target_event_title
    ):
        scope = extraction.reminder_scope or extraction.target_event_title
        events = sq.find_events(db, user, text=None if _is_blanket(scope) else scope)
        if not events:
            return {"ok": False, "message": f"No upcoming classes matching \"{scope}\".", "updated": 0}

        if extraction.intent == "DELETE_REMINDER":
            removed = sum(clear_event_reminders(db, e) for e in events)
            db.commit()
            return {
                "ok": True, "action": "reminders_off", "updated": removed,
                "message": f"Reminders turned off for {len(events)} upcoming class(es).",
            }

        minutes = extraction.reminder_minutes_before or user.default_reminder_minutes or 30
        for e in events:
            set_reminder_lead(db, e, user, minutes)
        db.commit()
        return {
            "ok": True, "action": "reminders_set", "updated": len(events),
            "message": f"Reminders set to {minutes} minutes before {len(events)} upcoming class(es).",
        }

    # Models often express "every Mon, Thu and Fri" as three separate event
    # objects that EACH carry all three recurrence_days, which would expand to
    # one copy per day per object. Collapse events that resolve to the same
    # schedule before writing anything.
    unique_events = []
    seen_specs = set()
    for evt in extraction.events:
        spec = (
            (evt.title or "").strip().lower(),
            evt.start_time,
            evt.end_time,
            evt.recurrence,
            tuple(sorted(d.lower() for d in (evt.recurrence_days or []))),
            evt.date,
            (evt.day or "").lower() if not evt.recurrence_days else None,
        )
        if spec in seen_specs:
            continue
        seen_specs.add(spec)
        unique_events.append(evt)

    for evt in unique_events:
        start_dt, end_dt, recurrence_rule = _schedule_event_to_datetimes(evt, user.timezone)
        duration = end_dt - start_dt
        occurrence_starts = generate_occurrence_starts(start_dt, recurrence_rule)
        group_id = str(uuid4()) if recurrence_rule and len(occurrence_starts) > 1 else None

        if recurrence_rule and recurrence_rule.startswith("weekly:"):
            series_count += len(recurrence_rule.split(":", 1)[1].split(","))
        else:
            series_count += 1

        for occ_start in occurrence_starts:
            # Guard against re-submitting the same extraction (an impatient
            # second click on Confirm) creating a parallel set of events.
            already_exists = (
                db.query(Event)
                .filter(
                    Event.user_id == user.id,
                    Event.title == evt.title,
                    Event.start_datetime == occ_start,
                    Event.is_cancelled.is_(False),
                )
                .first()
            )
            if already_exists:
                continue

            event = Event(
                user_id=user.id,
                title=evt.title,
                description=evt.description,
                event_type=evt.event_type,
                subject=evt.subject,
                start_datetime=occ_start,
                end_datetime=occ_start + duration,
                faculty=evt.faculty,
                location=evt.location,
                location_detail=evt.location_detail,
                location_url=evt.location_url,
                priority=evt.priority,
                recurrence_rule=recurrence_rule,
                recurrence_group_id=group_id,
            )
            db.add(event)
            db.flush()
            # Fall back to the professor's configured lead time when the model
            # didn't specify one, so every scheduled item actually reminds.
            lead = evt.reminder_minutes or user.default_reminder_minutes
            schedule_event_reminders(db, event, user, leads=[lead] if lead else None)
            created_events.append(event)

    for r in extraction.reminders:
        target_date = resolve_date(r.date, user.timezone)
        target_time = resolve_time(r.time)
        reminder_dt = combine(target_date, target_time, user.timezone)

        related_event = None
        if r.related_event_title:
            related_event = (
                db.query(Event)
                .filter(Event.user_id == user.id, Event.title.ilike(f"%{r.related_event_title}%"))
                .order_by(Event.start_datetime)
                .first()
            )
        if related_event and r.minutes_before_event:
            reminder = create_reminder_for_event(db, related_event, r.minutes_before_event)
        else:
            from app.models import Reminder

            reminder = Reminder(user_id=user.id, title=r.title, reminder_datetime=reminder_dt)
            db.add(reminder)
        created_reminders.append(reminder)

    for t in extraction.tasks:
        due = resolve_date(t.due_date, user.timezone) if t.due_date else None
        task = Task(
            user_id=user.id,
            title=t.title,
            priority=t.priority,
            due_date=combine(due, time(23, 59), user.timezone) if due else None,
        )
        db.add(task)
        db.flush()
        schedule_task_reminders(db, task, user)
        created_tasks.append(task)

    db.commit()

    return {
        "ok": True,
        "events_created": series_count,
        "reminders_created": len(created_reminders),
        "tasks_created": len(created_tasks),
    }


@router.post("/generate-timetable")
def ai_generate_timetable(payload: TimetableGenerateRequest, user: User = Depends(get_current_user)):
    return generate_timetable(payload)


@router.post("/find-free-time")
def ai_find_free_time(payload: FreeTimeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target_date = resolve_date(payload.date, user.timezone) if payload.date else date.today()
    day_start = resolve_time(user.working_hours_start)
    day_end = resolve_time(user.working_hours_end)
    slots = find_free_slots(db, user.id, target_date, payload.duration_minutes, user.timezone, day_start, day_end)
    return {
        "date": target_date.isoformat(),
        "duration_minutes": payload.duration_minutes,
        "free_slots": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in slots],
    }


@router.post("/resolve-conflict")
def ai_resolve_conflict(event_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):


    from app.services.conflict_service import suggest_resolution

    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user.id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    conflicts = find_conflicts(db, user.id, event.start_datetime, event.end_datetime, exclude_event_id=event.id)
    if not conflicts:
        return {"has_conflict": False}
    return {
        "has_conflict": True,
        "resolution": suggest_resolution(db, user.id, conflicts[0], (event.start_datetime, event.end_datetime)),
    }


@router.post("/query-schedule")
def ai_query_schedule(payload: AIPromptRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Lightweight schedule query: relies on process_prompt's target_date, falls back to 'this week'."""
    allowed, refusal = check_prompt(payload.prompt)
    if not allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, refusal)

    ai = get_ai_service()
    context = {"today": date.today().isoformat(), "weekday": date.today().strftime("%A"), "timezone": user.timezone}
    extraction = ai.process_prompt(payload.prompt, context)

    target_date = resolve_date(extraction.target_date, user.timezone) if extraction.target_date else date.today()
    start = combine(target_date, time.min, user.timezone)
    end = start + timedelta(days=7 if "week" in payload.prompt.lower() else 1)

    events = (
        db.query(Event)
        .filter(Event.user_id == user.id, Event.is_cancelled.is_(False), Event.start_datetime >= start, Event.start_datetime < end)
        .order_by(Event.start_datetime)
        .all()
    )
    return {
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "events": [
            {"id": e.id, "title": e.title, "start": e.start_datetime.isoformat(), "end": e.end_datetime.isoformat()}
            for e in events
        ],
    }
