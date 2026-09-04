"""Who an administrator may see and act on.

There are two kinds of administrator. A super admin (`is_admin`) runs the
platform. A college admin (`admin_college_id`) runs one college: the same
panel, over their own people only.

Every rule lives here rather than in the route handlers. The admin router has
eight endpoints that each take a user id, and a scoping check that has to be
remembered eight times is a check that will be forgotten once -- which in this
module means handing one college's administrator the keys to another's.

The restrictions on a college admin are not tidiness. Each one closes a way to
become a super admin or to reach outside the college:

  * They never act on an administrator of any kind. Resetting a super admin's
    password is a complete account takeover, and it would be reachable from a
    college panel the moment that super admin happened to sit in the college.
  * They never write is_admin, admin_college_id, college_id or college. The
    first makes them a peer of the platform owner, the second appoints
    administrators, and the last two move a person -- or themselves -- between
    colleges, which is the boundary everything else here is measured against.
  * Appointing a college admin is a super admin's act alone. Otherwise the
    first college admin could mint the rest.

A user outside the scope is reported as missing rather than forbidden. "You
may not touch this account" still confirms the account exists, and an admin
panel that answers that question for every id is a way to enumerate the
platform's users one guess at a time.
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Query, Session

from app.models import College, User

SUPER_ADMIN = "super_admin"
COLLEGE_ADMIN = "college_admin"


def role_of(user: User) -> str | None:
    """None for someone with no panel at all."""
    if user.is_admin:
        return SUPER_ADMIN
    if user.admin_college_id:
        return COLLEGE_ADMIN
    return None


def is_panel_admin(user: User) -> bool:
    return role_of(user) is not None


def scope_users(query: Query, admin: User) -> Query:
    """Narrow a User query to what this administrator may see.

    A college admin sees their own college and nothing else -- including, and
    this is the case worth being explicit about, not the accounts with no
    college at all. Those are new sign-ups who have not chosen one yet; they
    belong to nobody's college, so they are nobody's to manage.
    """
    if admin.is_admin:
        return query
    return query.filter(User.college_id == admin.admin_college_id)


def may_manage(admin: User, target: User) -> bool:
    """Whether `admin` may write to, reset or delete `target`."""
    if admin.is_admin:
        return True
    if not admin.admin_college_id:
        return False
    if target.college_id != admin.admin_college_id:
        return False
    # Never another administrator, of either kind. A college admin's authority
    # is over the people of their college, not over its other administrators.
    return not target.is_admin and not target.admin_college_id


def load_visible(db: Session, admin: User, user_id: str) -> User:
    """Fetch a user this administrator is allowed to have looked up."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not admin.is_admin and user.college_id != admin.admin_college_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


def load_manageable(db: Session, admin: User, user_id: str) -> User:
    """Fetch a user this administrator is allowed to change."""
    user = load_visible(db, admin, user_id)
    if not may_manage(admin, user):
        # Visible but out of reach: the account is in their college, so saying
        # so discloses nothing they cannot already see in their own list.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Administrator accounts can only be changed by a super admin.",
        )
    return user


# Fields a college admin may never write. is_admin and admin_college_id are
# the two that grant power; college and college_id are the two that move a
# person across the boundary that power is scoped by.
PRIVILEGED_FIELDS = ("is_admin", "admin_college_id", "college_id", "college")


def reject_privileged(admin: User, updates: dict) -> None:
    """Refuse rather than silently drop.

    Quietly ignoring the field would report success for a change that did not
    happen, and an administrator who believes they have granted or revoked
    something that they have not is worse off than one who was told no.
    """
    if admin.is_admin:
        return
    attempted = [f for f in PRIVILEGED_FIELDS if f in updates]
    if attempted:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Only a super admin can change {', '.join(attempted)}.",
        )


def admin_college(db: Session, user: User) -> College | None:
    if not user.admin_college_id:
        return None
    return db.get(College, user.admin_college_id)
