"""
Admin-only endpoints: full user management and system-wide statistics.

Every route here depends on `get_current_admin`, which 403s for non-admin
accounts. Regular professors are unaffected — their own routes remain
scoped to their own user_id.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_admin
from app.models import AIConversation, Event, Reminder, Task, User
from app.schemas import (
    AdminCreateUser,
    AdminPasswordReset,
    AdminStats,
    AdminUserOut,
    AdminUserUpdate,
)
from app.security import hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _to_admin_out(db: Session, user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        department=user.department,
        designation=user.designation,
        college=user.college,
        timezone=user.timezone,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        event_count=db.query(Event).filter(Event.user_id == user.id).count(),
        task_count=db.query(Task).filter(Task.user_id == user.id).count(),
    )


@router.get("/stats", response_model=AdminStats)
def system_stats(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    return AdminStats(
        total_users=db.query(User).count(),
        active_users=db.query(User).filter(User.is_active.is_(True)).count(),
        admin_users=db.query(User).filter(User.is_admin.is_(True)).count(),
        total_events=db.query(Event).count(),
        total_tasks=db.query(Task).count(),
        total_reminders=db.query(Reminder).count(),
        pending_reminders=db.query(Reminder).filter(Reminder.is_sent.is_(False)).count(),
        ai_conversations=db.query(AIConversation).count(),
        new_users_this_week=db.query(User).filter(User.created_at >= week_ago).count(),
    )


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    q: str | None = Query(default=None, description="Search name or email"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    query = db.query(User)
    if q:
        like = f"%{q}%"
        query = query.filter((User.name.ilike(like)) | (User.email.ilike(like)))
    users = query.order_by(User.created_at.desc()).all()
    return [_to_admin_out(db, u) for u in users]


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: AdminCreateUser, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An account with this email already exists")

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        department=payload.department,
        designation=payload.designation,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _to_admin_out(db, user)


@router.get("/users/{user_id}", response_model=AdminUserOut)
def get_user(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return _to_admin_out(db, user)


@router.put("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    updates = payload.model_dump(exclude_unset=True)

    # Guard against an admin locking everyone out of the admin panel.
    if user.id == admin.id:
        if updates.get("is_admin") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove your own admin rights")
        if updates.get("is_active") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")

    if updates.get("is_admin") is False:
        remaining = db.query(User).filter(User.is_admin.is_(True), User.id != user.id).count()
        if remaining == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot remove the last administrator")

    if "email" in updates:
        clash = db.query(User).filter(User.email == updates["email"], User.id != user.id).first()
        if clash:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That email is already in use")

    for field, value in updates.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return _to_admin_out(db, user)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    payload: AdminPasswordReset,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "message": f"Password reset for {user.email}"}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if user.is_admin:
        remaining = db.query(User).filter(User.is_admin.is_(True), User.id != user.id).count()
        if remaining == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete the last administrator")

    # Cascade deletes remove the user's events, tasks, reminders, and AI history.
    db.delete(user)
    db.commit()
    return None


@router.get("/users/{user_id}/events")
def user_events(user_id: str, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Read-only window into a professor's schedule, for support purposes."""
    if not db.get(User, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    events = (
        db.query(Event)
        .filter(Event.user_id == user_id)
        .order_by(Event.start_datetime.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": e.id,
            "title": e.title,
            "event_type": e.event_type,
            "start": e.start_datetime.isoformat(),
            "end": e.end_datetime.isoformat(),
        }
        for e in events
    ]
