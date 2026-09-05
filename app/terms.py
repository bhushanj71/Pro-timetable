"""Who has to agree to the terms, and when.

Two rules, and the second is the one worth writing down.

An account agrees once, and what it agreed to is recorded alongside the fact
that it did. A revision changes TERMS_VERSION, and everyone is asked again --
"they accepted" without saying what they accepted is not a record of anything.

And accounts that predate the terms are never asked. Not because their consent
matters less, but because the alternative is a notice thrown across the screen
of every professor mid-term for something they did not sign up to; if these
terms ever need affirmative agreement from existing accounts, that is a
decision to take deliberately, by moving TERMS_EFFECTIVE_AT back, rather than
one that arrives as a side effect of adding a page.
"""
from datetime import datetime, timezone

from app.models import User

# Bump this when the terms change in a way people should see. Both halves
# matter: the version is what is stored against an acceptance, and the date is
# what decides whether an account is old enough to be left alone.
TERMS_VERSION = "2026-09-05"
TERMS_EFFECTIVE_AT = datetime(2026, 9, 5, tzinfo=timezone.utc)


def record_acceptance(user: User, *, now: datetime | None = None) -> None:
    """Write down that this professor agreed, and to which version."""
    user.terms_accepted_at = now or datetime.now(timezone.utc)
    user.terms_version = TERMS_VERSION


def needs_acceptance(user: User | None) -> bool:
    """Whether this account still has to agree before it can be used.

    Signing up through the form agrees on the way in, so the only accounts
    that reach here unanswered are the ones created by Google sign-in -- which
    can start from the sign-in page and never sees the form at all. That path
    is the whole reason this is a server-side check on the account rather than
    a required box on one template.
    """
    if user is None:
        return False
    if user.terms_accepted_at and user.terms_version == TERMS_VERSION:
        return False

    created = user.created_at
    if created is None:
        return False
    # SQLite hands back naive datetimes even from a timezone-aware column, and
    # comparing one of those to an aware one raises rather than answering.
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created >= TERMS_EFFECTIVE_AT
