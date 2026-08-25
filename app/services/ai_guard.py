"""
Scope guard for the AI assistant.

The assistant exists to turn a professor's words into schedule changes. It is
not a general chatbot, and its prompt is attacker-reachable in the ordinary
case too: text arrives from imported CSVs and shared calendars, not only from
the person typing.

This runs before the model is called, so it costs nothing per request and
holds even when no API key is configured and the rule-based parser is doing
the work. It is deliberately a coarse filter -- refusing a legitimate
scheduling request would be worse than letting an odd one through -- so it
only rejects prompts that either try to change the assistant's instructions
or contain no scheduling content at all.
"""
import re

OUT_OF_SCOPE = "OUT_OF_SCOPE"

# Attempts to redirect the assistant rather than use it. Matched on the whole
# prompt, case-insensitively.
_INJECTION_PATTERNS = [
    r"\bignore\s+(all\s+|any\s+|the\s+)?(previous|prior|earlier|above)\b",
    r"\bdisregard\s+(all\s+|any\s+|the\s+)?(previous|prior|earlier|above)\b",
    r"\bforget\s+(everything|all|your|the)\s+(you|instructions?|rules?|prompt)",
    r"\byou\s+are\s+now\b",
    r"\bact\s+as\s+(a|an|the)\b",
    r"\bpretend\s+(to\s+be|you|that)\b",
    r"\bnew\s+(instructions?|rules?|persona|system\s+prompt)\b",
    r"\b(system|developer)\s*(prompt|message)\b",
    r"\breveal|repeat|print|show\b[^.?!]{0,30}\b(prompt|instructions?|rules?)\b",
    r"\bjailbreak|\bDAN\b|\bdeveloper\s+mode\b",
    r"\bdisable\s+(your\s+)?(safety|filter|guard|restrictions?)\b",
    r"</?\s*(system|assistant|user)\s*>",
    r"\[\s*(system|inst|/inst)\s*\]",
]

# Requests that are clearly for a general-purpose assistant.
_OFF_TOPIC_PATTERNS = [
    r"\bwrite\s+(me\s+)?(a|an|some)\s+(poem|song|story|essay|joke|script|novel|rap)\b",
    r"\b(translate|summari[sz]e)\s+(this|the\s+following)\b",
    r"\b(write|generate|give\s+me)\s+(me\s+)?(a|an|some)?\s*(python|java|c\+\+|javascript|sql|html|bash|code|script|program|function)\b",
    r"\bwho\s+(is|was)\s+the\s+(president|prime\s+minister|ceo)\b",
    r"\bwhat\s+is\s+the\s+(capital|population|weather|temperature)\b",
    r"\b(recipe|cook|bake)\b",
    r"\bmedical\s+advice|\bdiagnos(e|is)\b",
    r"\bstock\s+(price|tip)|\binvest(ment)?\s+advice\b",
    r"\btell\s+me\s+a\s+joke\b",
]

# Vocabulary that makes a prompt plausibly about this application. Kept broad:
# the cost of a false negative (refusing real work) is higher than the cost of
# passing an odd prompt to a model that returns strict JSON anyway.
_IN_SCOPE_WORDS = {
    "class", "classes", "lecture", "lectures", "lab", "labs", "practical",
    "meeting", "meetings", "seminar", "workshop", "fdp", "conference",
    "exam", "exams", "examination", "test", "viva", "review", "reviews",
    "project", "deadline", "submission", "task", "tasks", "todo",
    "remind", "reminder", "reminders", "notify", "alert",
    "schedule", "scheduled", "reschedule", "timetable", "calendar", "agenda",
    "event", "events", "appointment", "slot", "slots", "free", "busy",
    "book", "cancel", "cancelled", "postpone", "move", "shift", "delete",
    "remove", "update", "change", "add", "create", "plan", "arrange",
    "today", "tomorrow", "yesterday", "tonight", "morning", "afternoon",
    "evening", "week", "weekly", "month", "monthly", "daily", "recurring",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "am", "pm", "oclock", "hour", "hours", "minute", "minutes",
    "room", "hall", "venue", "department", "semester", "batch", "students",
    "subject", "syllabus", "attendance", "invigilation", "duty",
    "what", "when", "where", "show", "list", "next", "upcoming",
    # Location, faculty and conflict vocabulary, so "where do I need to go"
    # and "any clashes this week" are not mistaken for off-topic chat.
    "location", "map", "maps", "building", "floor", "block", "campus",
    "classroom", "lab", "navigate", "directions", "go", "reach",
    "faculty", "prof", "professor", "teacher", "sir", "madam",
    "conflict", "conflicts", "clash", "clashes", "overlap", "double",
    "before", "after", "turn", "off", "on", "set", "search", "find",
}

_WORD_RE = re.compile(r"[a-z]+")
_TIME_RE = re.compile(r"\b\d{1,2}\s*(:\d{2})?\s*(am|pm)\b|\b\d{1,2}:\d{2}\b|\b\d{4}-\d{2}-\d{2}\b")


def _matches(patterns: list[str], text: str) -> str | None:
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return pat
    return None


def check_prompt(prompt: str) -> tuple[bool, str | None]:
    """Return (allowed, refusal_message).

    Refusals are phrased for the professor, not the attacker: they say what
    the assistant does, so a genuine user who phrased something oddly knows
    how to rephrase it.
    """
    text = (prompt or "").strip()
    if not text:
        return False, "Tell me what you'd like to schedule — for example, “DSV lecture Monday 10am in A-301”."

    if _matches(_INJECTION_PATTERNS, text):
        return False, (
            "I only handle your ProfSchedule timetable — classes, meetings, exams, "
            "tasks and reminders. I can't take on other instructions or roles."
        )

    if _matches(_OFF_TOPIC_PATTERNS, text):
        return False, (
            "That's outside what I do. I can add, move, cancel or look up lectures, "
            "labs, meetings, exams, deadlines, tasks and reminders in your timetable."
        )

    # A prompt with no scheduling vocabulary and no time reference is not a
    # schedule request. Short prompts are given the benefit of the doubt --
    # "9am?" and "A-301" are terse but legitimate follow-ups.
    words = set(_WORD_RE.findall(text.lower()))
    if len(words) > 3 and not (words & _IN_SCOPE_WORDS) and not _TIME_RE.search(text.lower()):
        return False, (
            "I couldn't find anything about your schedule in that. Try naming what and when — "
            "for example, “move Friday's DSV lab to 2pm” or “what's on tomorrow?”."
        )

    return True, None
