"""
Colleges, departments, and each user's place in them.

Reads are open to any signed-in user, because the pickers need them. Writes
are checked here on the server for every call -- a hidden button is not a
permission.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func

from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import College, Department, OrgStatus, User
from app.services import org_service as og

router = APIRouter(prefix="/api/org", tags=["organisation"])


# ---------------------------------------------------------------------------
# Pickers
# ---------------------------------------------------------------------------
@router.get("/colleges")
def colleges(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"colleges": [og.college_dict(c) for c in og.list_colleges(db)]}


@router.get("/colleges/{college_id}/departments")
def departments(college_id: str, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    if not db.get(College, college_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such college.")
    return {"departments": [og.department_dict(d) for d in og.list_departments(db, college_id)]}


# ---------------------------------------------------------------------------
# The signed-in user's own work profile
# ---------------------------------------------------------------------------
@router.get("/profile")
def my_work_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """What Work knows about this person, and whether it is enough."""
    return {
        "complete": og.profile_complete(user),
        "role": og.role_of(user),
        "name": user.name,
        "college": og.college_dict(user.college_rel) if user.college_rel else None,
        "department": og.department_dict(user.department_rel) if user.department_rel else None,
        # Offered so the panel can preselect when there is only one choice.
        "colleges": [og.college_dict(c) for c in og.list_colleges(db)],
    }


class WorkProfileIn(BaseModel):
    college_id: str = Field(min_length=1, max_length=36)
    department_id: str = Field(min_length=1, max_length=36)


@router.put("/profile")
def set_my_work_profile(payload: WorkProfileIn, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Set your own, and only your own.

    There is no user_id in the payload on purpose: the account edited is the
    account making the call, so no request can move somebody else between
    departments.
    """
    og.set_work_profile(db, user, payload.college_id, payload.department_id)
    return {
        "ok": True,
        "complete": True,
        "college": og.college_dict(user.college_rel),
        "department": og.department_dict(user.department_rel),
    }


# ---------------------------------------------------------------------------
# Admin: colleges
# ---------------------------------------------------------------------------
@router.get("/manage/colleges")
def manage_colleges(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every college an administrator may act on, with what is inside it."""
    role = og.role_of(user)
    if role == og.USER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrators only.")

    dept_counts, member_counts = og.college_counts(db)
    rows = og.list_colleges(db, include_archived=True)
    if role == og.COLLEGE_ADMIN:
        rows = [c for c in rows if c.id == user.admin_college_id]

    return {
        "role": role,
        "can_create_college": role == og.SUPER_ADMIN,
        "colleges": [
            og.college_dict(c, departments=dept_counts.get(c.id, 0), members=member_counts.get(c.id, 0))
            for c in rows
        ],
    }


class CollegeIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)


@router.post("/colleges", status_code=status.HTTP_201_CREATED)
def add_college(payload: CollegeIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    og.require_super_admin(user)
    return og.college_dict(og.create_college(db, payload.name, payload.location))


class CollegeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    status: str | None = None


@router.put("/colleges/{college_id}")
def edit_college(college_id: str, payload: CollegeUpdate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    college = db.get(College, college_id)
    if not college:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such college.")
    og.require_college_admin(user, college_id)

    if payload.status and payload.status not in {s.value for s in OrgStatus}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown status.")
    return og.college_dict(og.update_college(
        db, college, name=payload.name, location=payload.location, status_=payload.status))


# ---------------------------------------------------------------------------
# Admin: departments
# ---------------------------------------------------------------------------
@router.get("/manage/colleges/{college_id}/departments")
def manage_departments(college_id: str, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    college = db.get(College, college_id)
    if not college:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such college.")
    og.require_college_admin(user, college_id)

    rows = og.list_departments(db, college_id, include_archived=True)
    counts = dict(
        db.query(User.department_id, func.count(User.id))
        .filter(User.department_id.isnot(None))
        .group_by(User.department_id)
        .all()
    )
    return {
        "college": og.college_dict(college),
        "departments": [og.department_dict(d, members=counts.get(d.id, 0)) for d in rows],
    }


class DepartmentIn(BaseModel):
    college_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=200)
    # An administrative post rather than a teaching department. Defaulted, so
    # every existing caller keeps creating departments without knowing this
    # field is here.
    kind: Literal["academic", "office"] = "academic"


@router.post("/departments", status_code=status.HTTP_201_CREATED)
def add_department(payload: DepartmentIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    college = db.get(College, payload.college_id)
    if not college:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such college.")
    og.require_college_admin(user, college.id)
    return og.department_dict(og.create_department(db, college, payload.name, payload.kind))


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    status: str | None = None


@router.put("/departments/{department_id}")
def edit_department(department_id: str, payload: DepartmentUpdate,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    dept = db.get(Department, department_id)
    if not dept:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such department.")
    og.require_college_admin(user, dept.college_id)

    if payload.status and payload.status not in {s.value for s in OrgStatus}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown status.")
    updated = og.update_department(db, dept, name=payload.name, status_=payload.status)
    return og.department_dict(updated, members=og.department_member_count(db, updated.id))


@router.delete("/departments/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_department(department_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    dept = db.get(Department, department_id)
    if not dept:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such department.")
    og.require_college_admin(user, dept.college_id)
    og.delete_department(db, dept)
    return None


# ---------------------------------------------------------------------------
# Admin: who is where
# ---------------------------------------------------------------------------
@router.get("/manage/members")
def manage_members(
    college_id: str | None = None,
    department_id: str | None = None,
    q: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    role = og.role_of(user)
    if role == og.USER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrators only.")
    # A college admin is scoped to their own college whatever they ask for.
    if role == og.COLLEGE_ADMIN:
        college_id = user.admin_college_id

    rows = db.query(User)
    if college_id:
        rows = rows.filter(User.college_id == college_id)
    if department_id:
        rows = rows.filter(User.department_id == department_id)
    if q:
        rows = rows.filter(User.name.ilike(f"%{q.strip()}%"))

    found = rows.order_by(User.name).limit(200).all()
    return {"members": [og.person_dict(u, with_college=True) for u in found]}
