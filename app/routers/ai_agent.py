"""The agent endpoint.

Separate from the existing /api/ai router rather than folded into it. That one
turns a sentence into a validated extraction the user then confirms, and it
works; this one reasons over the professor's data across several steps. They
answer different questions and fail in different ways, and putting the new one
alongside means nothing that works today can be broken by it.
"""
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import AIConversation, User
from app.rate_limit import RateLimiter
from app.services.agent import runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai-agent"])

# A single request here can mean several model calls and several queries, so it
# is metered more tightly than an ordinary endpoint. Per user, not per address:
# a shared campus network is one address.
_limiter = RateLimiter(max_attempts=20, window_seconds=300)


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


@router.post("/agent")
def ask_agent(payload: AgentRequest, request: Request,
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Ask a question about your own schedule, tasks or reminders.

    Read-only for now: the tools this can reach only read. Nothing here can
    change a schedule, so nothing here needs a confirmation step yet -- that
    arrives with the writing tools, together with the levels that gate them.
    """
    # Raises 429 with a Retry-After of its own, which is the convention the
    # rest of this application already answers with.
    _limiter.check(request, extra=f"agent:{user.id}")

    outcome = runner.run(db, user, payload.message)

    # Kept for the professor's own history and for working out later what the
    # assistant actually did. The transcript is not stored: it is the model's
    # working, and the steps already say what was read.
    try:
        db.add(AIConversation(
            user_id=user.id,
            prompt=payload.message[:2000],
            ai_response=outcome.answer[:4000],
            intent="AGENT",
        ))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("agent: could not record the conversation", exc_info=True)

    return outcome.public()


@router.get("/agent/tools")
def list_tools(user: User = Depends(get_current_user)):
    """What the assistant can currently do.

    Exposed because "what can it do" is a fair question with a factual answer,
    and because a catalogue that drifts from the code is the first thing to go
    wrong in a system like this -- here there is only one list.
    """
    from app.services.agent import tools as tk

    return {"tools": [
        {"name": t["name"], "description": t["description"]}
        for t in tk.catalogue(read_only_only=True)
    ]}
