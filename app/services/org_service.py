"""
College -> Department -> Member: the organisation behind Work mode.

Two rules shape this module.

A department is an identity, not a permission. It says who someone is so an
assignor can tell two people named Rahul apart, and it lets a member list be
filtered. It never decides who may work with whom: a Computer Engineering
professor and a Mechanical Engineering professor in the same community
collaborate exactly as before.

A department is never guessed. Existing accounts carry free text that usually
resolves, and where it does the link is made for them. Where it does not, the
field stays empty and Work asks -- being filed under the wrong department is
worse than being asked which one you are in.
"""
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import (
    College,
    Department,
    OrgStatus,
    User,
    normalise_org_name,
)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
SUPER_ADMIN = "super_admin"
COLLEGE_ADMIN = "college_admin"
USER = "user"


def role_of(user: User) -> str:
    if user.is_admin:
        return SUPER_ADMIN
    return COLLEGE_ADMIN if user.admin_college_id else USER


def require_super_admin(user: User) -> None:
    """Creating a college is a platform-level act, not a college-level one."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a super admin can do that.")


def require_college_admin(user: User, college_id: str) -> None:
    """A super admin manages every college; a college admin manages one."""
    if user.is_admin:
        return
    if user.admin_college_id and user.admin_college_id == college_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't administer that college.")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def list_colleges(db: Session, *, include_archived: bool = False) -> list[College]:
    q = db.query(College)
    if not include_archived:
        q = q.filter(College.status == OrgStatus.ACTIVE.value)
    return q.order_by(College.name).all()


def list_departments(db: Session, college_id: str, *, include_archived: bool = False) -> list[Department]:
    q = db.query(Department).filter(Department.college_id == college_id)
    if not include_archived:
        q = q.filter(Department.status == OrgStatus.ACTIVE.value)
    return q.order_by(Department.name).all()


def college_dict(c: College, *, departments: int | None = None, members: int | None = None) -> dict:
    out = {
        "id": c.id, "name": c.name, "location": c.location, "status": c.status,
    }
    if departments is not None:
        out["department_count"] = departments
    if members is not None:
        out["member_count"] = members
    return out


def department_dict(d: Department, *, members: int | None = None) -> dict:
    out = {"id": d.id, "college_id": d.college_id, "name": d.name, "status": d.status}
    if members is not None:
        out["member_count"] = members
    return out


# ---------------------------------------------------------------------------
# How a person is shown, everywhere in Work
# ---------------------------------------------------------------------------
def person_dict(u: User, *, with_college: bool = False) -> dict:
    """Name and department together.

    The department travels with the name rather than being fetched separately,
    because every place that shows a person -- member lists, the assign
    picker, task detail, the directory -- needs it for the same reason: two
    colleagues can share a name, and only one of them is the one you meant.

    Still no email. That rule has not changed.
    """
    out = {
        "id": u.id,
        "name": u.name,
        "initial": (u.name or "?")[:1].upper(),
        "designation": u.designation,
        "department": u.department_rel.name if u.department_rel else None,
        "department_id": u.department_id,
    }
    if with_college:
        out["college"] = u.college_rel.name if u.college_rel else None
        out["college_id"] = u.college_id
    return out


PERSON_LOADERS = (selectinload(User.department_rel), selectinload(User.college_rel))


# ---------------------------------------------------------------------------
# The user's own work profile
# ---------------------------------------------------------------------------
def profile_complete(user: User) -> bool:
    """Both halves, or neither counts. A department without a college cannot
    be validated, and a college without a department is what this exists to
    collect."""
    return bool(user.college_id and user.department_id)


def set_work_profile(db: Session, user: User, college_id: str, department_id: str) -> User:
    """Validate and store. Both checks run here, not in the route, so every
    caller gets them -- the onboarding panel, the profile page and any future
    admin edit."""
    college = db.get(College, college_id) if college_id else None
    if not college:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a college.")
    if college.status != OrgStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That college is no longer active.")

    department = db.get(Department, department_id) if department_id else None
    if not department:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a department.")
    # The check the whole relational model exists to make possible: a
    # department id alone proves nothing about which college it sits under.
    if department.college_id != college.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That department belongs to a different college.",
        )
    if department.status != OrgStatus.ACTIVE.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"“{department.name}” has been archived. Pick a current department.",
        )

    user.college_id = college.id
    user.department_id = department.id
    # Kept in step so the personal profile, the exports and the admin list --
    # all of which read the free-text columns -- keep showing the right thing
    # without each having to learn about these tables.
    user.college = college.name
    user.department = department.name
    db.commit()
    db.refresh(user)
    return user


def require_work_profile(user: User) -> None:
    """Gate for the Work actions that place someone in the organisation.

    Deliberately not applied to reading a dashboard, answering an assignment
    or updating progress. Someone mid-way through accepted work must be able
    to finish it; a profile prompt that strands them would break exactly the
    thing this feature is supposed to enhance.
    """
    if not profile_complete(user):
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            "Choose your college and department before using Work.",
        )


# ---------------------------------------------------------------------------
# Admin writes
# ---------------------------------------------------------------------------
def create_college(db: Session, name: str, location: str | None) -> College:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the college a name.")

    key = normalise_org_name(name)
    if not key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That name has no letters or digits in it.")
    clash = db.query(College).filter(College.normalised_name == key).first()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, f"“{clash.name}” already exists.")

    college = College(
        name=name, normalised_name=key,
        location=(location or "").strip() or None,
        status=OrgStatus.ACTIVE.value,
    )
    db.add(college)
    db.commit()
    db.refresh(college)
    return college


def update_college(db: Session, college: College, *, name=None, location=None, status_=None) -> College:
    if name is not None:
        name = name.strip()
        key = normalise_org_name(name)
        if not key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the college a name.")
        clash = db.query(College).filter(
            College.normalised_name == key, College.id != college.id).first()
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, f"“{clash.name}” already exists.")
        college.name, college.normalised_name = name, key
    if location is not None:
        college.location = location.strip() or None
    if status_ is not None:
        college.status = status_
    db.commit()
    db.refresh(college)
    return college


def create_department(db: Session, college: College, name: str) -> Department:
    name = (name or "").strip()
    key = normalise_org_name(name)
    if not key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the department a name.")

    clash = db.query(Department).filter(
        Department.college_id == college.id, Department.normalised_name == key).first()
    if clash:
        # Re-activating beats creating a duplicate: an archived department
        # still has members and history attached to its id.
        if clash.status == OrgStatus.ARCHIVED.value:
            clash.status = OrgStatus.ACTIVE.value
            clash.name = name
            db.commit()
            db.refresh(clash)
            return clash
        raise HTTPException(status.HTTP_409_CONFLICT, f"“{clash.name}” is already in this college.")

    dept = Department(college_id=college.id, name=name, normalised_name=key,
                      status=OrgStatus.ACTIVE.value)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


def update_department(db: Session, dept: Department, *, name=None, status_=None) -> Department:
    if name is not None:
        name = name.strip()
        key = normalise_org_name(name)
        if not key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the department a name.")
        clash = db.query(Department).filter(
            Department.college_id == dept.college_id,
            Department.normalised_name == key,
            Department.id != dept.id,
        ).first()
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, f"“{clash.name}” is already in this college.")
        dept.name, dept.normalised_name = name, key
    if status_ is not None:
        dept.status = status_
    db.commit()
    db.refresh(dept)
    return dept


def department_member_count(db: Session, department_id: str) -> int:
    return db.query(User).filter(User.department_id == department_id).count()


def delete_department(db: Session, dept: Department) -> None:
    """Only when nothing points at it.

    A department with members cannot be deleted, because the rows that name it
    are people's profiles and their history in every community they belong to.
    Archiving is offered instead: it disappears from the pickers while every
    existing reference still resolves.
    """
    members = department_member_count(db, dept.id)
    if members:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{members} {'person is' if members == 1 else 'people are'} in “{dept.name}”. "
            "Archive it instead — deleting would leave their profiles pointing at nothing.",
        )
    db.delete(dept)
    db.commit()


def college_counts(db: Session) -> tuple[dict, dict]:
    """Department and member counts for every college, in two queries."""
    dept_rows = (
        db.query(Department.college_id, func.count(Department.id))
        .filter(Department.status == OrgStatus.ACTIVE.value)
        .group_by(Department.college_id)
        .all()
    )
    member_rows = (
        db.query(User.college_id, func.count(User.id))
        .filter(User.college_id.isnot(None))
        .group_by(User.college_id)
        .all()
    )
    return dict(dept_rows), dict(member_rows)
