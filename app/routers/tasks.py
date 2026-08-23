"""
CRUD for personal tasks (submit marks, prepare notes, etc.).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Task, User
from app.schemas import TaskCreate, TaskOut, TaskUpdate
from app.services.reminder_service import schedule_task_reminders

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    priority: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Task).filter(Task.user_id == user.id)
    if status_filter:
        query = query.filter(Task.status == status_filter)
    if priority:
        query = query.filter(Task.priority == priority)
    return query.order_by(Task.due_date.is_(None), Task.due_date).all()


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = Task(user_id=user.id, **payload.model_dump())
    db.add(task)
    db.flush()
    # Tasks created here previously had no reminders at all.
    schedule_task_reminders(db, task, user)
    db.commit()
    db.refresh(task)
    return task


@router.put("/{task_id}", response_model=TaskOut)
def update_task(task_id: str, payload: TaskUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(task, field, value)

    if updates.get("status") == "completed" and not task.completed_at:
        task.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    task.status = "completed"
    task.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    db.delete(task)
    db.commit()
    return None
