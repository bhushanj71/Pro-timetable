"""
Provider-agnostic AI service. Talks to any OpenAI-compatible chat completion
endpoint (OpenAI, NVIDIA NIM, Ollama, vLLM, etc.) selected purely via
environment variables — no provider is hard-coded.

If no AI_API_KEY is configured, falls back to a small rule-based extractor
so the app remains usable (with reduced accuracy) without an LLM key.

The AI never writes to the database directly: process_prompt() always
returns a validated AIExtractionResult that the router layer confirms with
the user before persisting anything.
"""
import json
import logging
import re
from datetime import datetime, timedelta

import httpx
from pydantic import ValidationError

from app.config import get_settings
from app.schemas import AIExtractionResult

settings = get_settings()
logger = logging.getLogger(__name__)

PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "ollama": "http://localhost:11434/v1",
}

SYSTEM_PROMPT = """You are the scheduling assistant inside ProfSchedule AI, used by college professors.
Convert the professor's natural-language request into STRICT JSON matching this schema, and nothing else:

{
  "intent": "CREATE_EVENT | UPDATE_EVENT | DELETE_EVENT | CREATE_REMINDER | DELETE_REMINDER | QUERY_SCHEDULE | GENERATE_TIMETABLE | FIND_FREE_TIME | CREATE_TASK | COMPLETE_TASK | CREATE_RECURRING_EVENT",
  "events": [
    {
      "title": "string",
      "event_type": "lecture | lab | meeting | project_review | examination | personal | research | deadline | conference | fdp | workshop | other",
      "subject": "string or null",
      "day": "Monday..Sunday, or 'tomorrow'/'today', or null for one-off dated events",
      "date": "YYYY-MM-DD or null",
      "start_time": "HH:MM 24-hour",
      "end_time": "HH:MM 24-hour",
      "recurrence": "weekly | daily | monthly | null",
      "recurrence_days": ["Monday", "Wednesday"] or null,
      "location": "string or null",
      "priority": "low | medium | high | urgent",
      "reminder_minutes": integer or null,
      "description": "string or null"
    }
  ],
  "reminders": [
    {"title": "string", "date": "YYYY-MM-DD or null", "time": "HH:MM or null", "minutes_before_event": int or null, "related_event_title": "string or null"}
  ],
  "tasks": [
    {"title": "string", "due_date": "YYYY-MM-DD or null", "priority": "low | medium | high | urgent"}
  ],
  "query_text": "string or null (for QUERY_SCHEDULE)",
  "duration_minutes": integer or null (for FIND_FREE_TIME),
  "target_date": "YYYY-MM-DD or null (for FIND_FREE_TIME / QUERY_SCHEDULE)",
  "notes": "one short human-readable sentence summarizing what you understood"
}

Rules:
- Today's date and the professor's timezone are given in the user message context — resolve relative dates ("tomorrow", "next Friday") yourself into actual YYYY-MM-DD dates.
- For recurring weekly lectures across multiple days, put ALL days in recurrence_days and set recurrence="weekly"; still include start_time/end_time.
- Only output valid JSON. No markdown fences, no commentary outside the JSON object.
- If the request is ambiguous, still produce your best-guess structured JSON, and mention the ambiguity in "notes".
"""


class AIServiceError(Exception):
    pass


class AIService:
    def __init__(self):
        self.provider = (settings.AI_PROVIDER or "none").lower()
        self.api_key = settings.AI_API_KEY
        self.model = settings.AI_MODEL
        self.base_url = settings.AI_BASE_URL or PROVIDER_BASE_URLS.get(self.provider)

    @property
    def is_configured(self) -> bool:
        return bool(self.provider != "none" and self.api_key and self.base_url)

    def process_prompt(self, prompt: str, user_context: dict) -> AIExtractionResult:
        if self.is_configured:
            try:
                raw = self._call_llm(prompt, user_context)
                return AIExtractionResult.model_validate(raw)
            except (httpx.HTTPError, ValidationError, json.JSONDecodeError, AIServiceError) as exc:
                # Log why we degraded — a silent fallback makes a misconfigured
                # model or key look like the AI simply parsing badly.
                logger.warning(
                    "AI provider call failed (%s: %s); using rule-based fallback",
                    type(exc).__name__,
                    exc,
                )

        raw = self._fallback_rule_based(prompt, user_context)
        return AIExtractionResult.model_validate(raw)

    def _call_llm(self, prompt: str, user_context: dict) -> dict:
        context_line = (
            f"Context: today is {user_context.get('today')} "
            f"({user_context.get('weekday')}), timezone is {user_context.get('timezone')}."
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{context_line}\n\nRequest: {prompt}"},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        with httpx.Client(timeout=60) as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise AIServiceError("Unexpected AI provider response shape") from exc

        return json.loads(content)

    # ------------------------------------------------------------------
    # Rule-based fallback (no LLM key configured)
    # ------------------------------------------------------------------
    def _fallback_rule_based(self, prompt: str, user_context: dict) -> dict:
        from app.services.nlp_dates import WEEKDAYS

        text = prompt.strip()
        lower = text.lower()

        days_found = [d.capitalize() for d in WEEKDAYS if d in lower]
        times = re.findall(r"(\d{1,2}(?::\d{2})?\s?(?:am|pm))", lower)

        # A prompt describing an event (lecture/meeting/etc, a weekday, "every", or
        # a time range) takes priority over a bare "remind" mention, since phrases
        # like "...every Monday... Remind me 30 minutes before" describe an event
        # WITH a reminder attached, not a standalone reminder. Only "remind me to
        # <do something>" with no event signal is a standalone CREATE_REMINDER.
        event_type_words = ("lecture", "class", "meeting", "lab", "review", "exam", "workshop", "conference", "fdp")
        has_event_signal = bool(days_found) or "every" in lower or any(w in lower for w in event_type_words) or len(times) >= 2

        intent = "CREATE_EVENT"
        if has_event_signal:
            intent = "CREATE_RECURRING_EVENT" if (days_found or "every" in lower) else "CREATE_EVENT"
        elif "remind" in lower:
            intent = "CREATE_REMINDER"
        elif "when am i free" in lower or "find" in lower and "free" in lower or "free time" in lower:
            intent = "FIND_FREE_TIME"
        elif "cancel" in lower or "delete" in lower:
            intent = "DELETE_EVENT"
        elif "move" in lower or "reschedule" in lower:
            intent = "UPDATE_EVENT"
        elif "what do i have" in lower or "schedule" in lower and "?" in lower:
            intent = "QUERY_SCHEDULE"
        elif "timetable" in lower and ("generate" in lower or "create" in lower):
            intent = "GENERATE_TIMETABLE"
        elif "task" in lower and "complete" in lower:
            intent = "COMPLETE_TASK"

        def to_24h(t: str) -> str:
            t = t.replace(" ", "")
            try:
                dt = datetime.strptime(t, "%I:%M%p") if ":" in t else datetime.strptime(t, "%I%p")
                return dt.strftime("%H:%M")
            except ValueError:
                return "09:00"

        start_time = to_24h(times[0]) if times else "09:00"
        if len(times) > 1:
            end_time = to_24h(times[1])
        else:
            end_dt = datetime.strptime(start_time, "%H:%M") + timedelta(hours=1)
            end_time = end_dt.strftime("%H:%M")

        day_field = days_found[0] if len(days_found) == 1 else None
        recurrence = "weekly" if (days_found or "every" in lower) else None

        # Title: strip filler words, day names, time tokens and reminder phrasing for a rough guess
        title_guess = text
        title_guess = re.sub(r"remind me\s*\d*\s*minutes?\s*before", "", title_guess, flags=re.IGNORECASE)
        title_guess = re.sub(
            r"\b(i have|i teach|remind me|schedule|create|add|every|on|at|from|to|tomorrow|today|and|minutes?|before)\b",
            "",
            title_guess,
            flags=re.IGNORECASE,
        )
        for day in WEEKDAYS:
            title_guess = re.sub(rf"\b{day}s?\b", "", title_guess, flags=re.IGNORECASE)
        title_guess = re.sub(r"\d{1,2}(:\d{2})?\s?(am|pm)\b", "", title_guess, flags=re.IGNORECASE)
        title_guess = re.sub(r"\s+", " ", title_guess).strip(" .,:")
        title_guess = title_guess[:120] or "New Event"

        if intent == "CREATE_REMINDER":
            target_day = "tomorrow" if "tomorrow" in lower else (day_field or "today")
            return {
                "intent": intent,
                "events": [],
                "reminders": [
                    {
                        "title": title_guess,
                        "date": None,
                        "time": start_time,
                        "minutes_before_event": None,
                        "related_event_title": None,
                    }
                ],
                "tasks": [],
                "target_date": target_day,
                "notes": f"Rule-based fallback parsed a reminder around {start_time}.",
            }

        if intent in ("FIND_FREE_TIME",):
            duration = 60
            hrs = re.search(r"(\d+)\s*hour", lower)
            mins = re.search(r"(\d+)\s*minute", lower)
            if hrs:
                duration = int(hrs.group(1)) * 60
            elif mins:
                duration = int(mins.group(1))
            target_day = "tomorrow" if "tomorrow" in lower else (day_field or "today")
            return {
                "intent": intent,
                "events": [],
                "reminders": [],
                "tasks": [],
                "duration_minutes": duration,
                "target_date": target_day,
                "notes": "Rule-based fallback estimated the requested free-time window.",
            }

        if intent == "QUERY_SCHEDULE":
            return {
                "intent": intent,
                "events": [],
                "query_text": text,
                "notes": "Rule-based fallback: showing your schedule for the requested period.",
            }

        # Default: CREATE_EVENT / CREATE_RECURRING_EVENT
        event = {
            "title": title_guess.title(),
            "event_type": "lecture" if "lecture" in lower else ("meeting" if "meeting" in lower else "other"),
            "subject": title_guess.title() if "lecture" in lower else None,
            "day": day_field,
            "date": "tomorrow" if ("tomorrow" in lower and not days_found) else None,
            "start_time": start_time,
            "end_time": end_time,
            "recurrence": recurrence,
            "recurrence_days": days_found or None,
            "location": None,
            "priority": "high" if "high" in lower or "urgent" in lower else "medium",
            "reminder_minutes": 30 if "remind" in lower else None,
            "description": None,
        }
        return {
            "intent": intent,
            "events": [event],
            "reminders": [],
            "tasks": [],
            "notes": "Parsed with the built-in rule-based fallback (no AI provider configured).",
        }


_ai_service_singleton: AIService | None = None


def get_ai_service() -> AIService:
    global _ai_service_singleton
    if _ai_service_singleton is None:
        _ai_service_singleton = AIService()
    return _ai_service_singleton
