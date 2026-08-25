"""
Work-mode command parsing.

Kept separate from the personal parser rather than folded into it. The two
modes must not be able to act on each other's data, and the cleanest guarantee
of that is that the code which creates a lecture and the code which assigns a
task never see the same request.

Intent detection here is rule-based and runs whatever AI provider is
configured, because these commands name concrete objects -- a community, a
person, a percentage -- and pattern matching reads them more reliably than a
model round trip.
"""
import re

WORK_INTENTS = (
    "CREATE_COMMUNITY",
    "INVITE_MEMBER",
    "ASSIGN_TASK",
    "VIEW_REQUESTS",
    "VIEW_MY_TASKS",
    "VIEW_ASSIGNED_BY_ME",
    "TASK_PROGRESS_QUERY",
    "SET_TASK_PROGRESS",
    "RESPOND_TASK",
    "VIEW_COMMUNITIES",
)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    out = text.strip().strip("\"'.,!?").strip()
    # Trailing scaffolding the patterns cannot avoid capturing.
    out = re.sub(r"\s+(task|community|please)$", "", out, flags=re.IGNORECASE).strip()
    if out.lower() in ("task", "my task", "the task", "it", "this"):
        return None
    return out or None


def parse_work_command(prompt: str) -> dict | None:
    """Return a work intent, or None if this isn't a work command.

    None matters: it lets the caller fall through to the personal parser, so a
    professor in Work mode can still ask about their own timetable.
    """
    text = (prompt or "").strip()
    low = text.lower()
    if not low:
        return None

    def out(intent, **kw):
        return {"intent": intent, **kw}

    # --- Create a community ---
    m = re.search(r"create (?:a )?(?:new )?(?:community|workspace|team)(?: called| named)?\s+(.+)", low)
    if m:
        return out("CREATE_COMMUNITY", name=_clean(text[m.start(1):]))

    # --- Invite ---
    m = re.search(r"(?:invite|add)\s+(.+?)\s+(?:to|into)\s+(.+)", low)
    if m and ("communit" in low or "team" in low or "workspace" in low or "@" in low or True):
        return out("INVITE_MEMBER",
                   person=_clean(text[m.start(1):m.end(1)]),
                   community=_clean(text[m.start(2):]))

    # --- Set progress: check before assignment, since "my task" also matches
    #     the assignment shape. ---
    m = re.search(r"(\d{1,3})\s*(?:%|percent)", low)
    if m and re.search(r"\b(mark|set|update|move)\b", low):
        pct = min(100, int(m.group(1)))
        t = re.search(r"(?:mark|set|update|move)\s+(?:my\s+)?(.+?)\s+(?:task\s+)?(?:as|to)\b", low)
        return out("SET_TASK_PROGRESS", progress=pct, task=_clean(text[t.start(1):t.end(1)]) if t else None)

    # --- Accept / decline ---
    m = re.search(r"\b(accept|decline|reject)\b\s+(?:the\s+)?(.+)", low)
    if m and "task" in low:
        return out("RESPOND_TASK",
                   accept=m.group(1) == "accept",
                   task=_clean(text[m.start(2):]))

    # --- Assign ---
    m = re.search(r"assign\s+(?:the\s+)?(.+?)\s+(?:task\s+)?to\s+(.+)", low)
    if m:
        people = re.split(r",|\band\b", text[m.start(2):])
        return out("ASSIGN_TASK",
                   title=_clean(text[m.start(1):m.end(1)]),
                   people=[p for p in (_clean(p) for p in people) if p])

    m = re.search(r"create (?:a )?group task\s+(?:called\s+)?(.+?)\s+for\s+(.+)", low)
    if m:
        people = re.split(r",|\band\b", text[m.start(2):])
        return out("ASSIGN_TASK",
                   title=_clean(text[m.start(1):m.end(1)]),
                   people=[p for p in (_clean(p) for p in people) if p])

    # --- Queries ---
    if re.search(r"\b(pending|waiting)\b.*\b(request|task|assignment)", low) or "task requests" in low:
        return out("VIEW_REQUESTS")
    if re.search(r"assigned by me|tasks i (?:have )?assigned|my assignments", low):
        return out("VIEW_ASSIGNED_BY_ME")
    if re.search(r"progress of|how is .* going|status of", low):
        t = re.search(r"(?:progress of|status of)\s+(?:the\s+)?(.+)", low)
        return out("TASK_PROGRESS_QUERY", task=_clean(text[t.start(1):]) if t else None)
    if re.search(r"my (?:active )?(?:work )?tasks|what am i working on", low):
        return out("VIEW_MY_TASKS")
    if re.search(r"my communities|show communities|which teams", low):
        return out("VIEW_COMMUNITIES")

    return None
