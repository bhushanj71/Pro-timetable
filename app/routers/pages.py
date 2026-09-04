"""
Server-rendered HTML pages (Jinja2). All data is fetched client-side from
the JSON API via fetch/HTMX so these views stay thin.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.deps import get_current_user_optional
from app.models import User
from app.services import admin_scope

router = APIRouter(tags=["pages"])
# Absolute path so the templates resolve regardless of working directory.
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _ctx(request: Request, user: User | None, **extra):
    # `v` busts the browser cache for static assets whenever they change.
    from datetime import datetime, timezone

    from app.main import ASSET_VERSION

    return {
        "request": request,
        "user": user,
        "v": ASSET_VERSION,
        "now_year": datetime.now(timezone.utc).year,
        # Which module this page belongs to. Taken from the path, not from the
        # user's stored active_profile: the path is where the reader actually
        # is, and the two disagree the moment somebody follows a link into Work
        # from a notification.
        "in_work": request.url.path.startswith("/work"),
        **extra,
    }


@router.get("/")
def index(request: Request, user: User | None = Depends(get_current_user_optional)):
    """The homepage is the default page for everyone.

    Signed-in professors still see it, but the calls to action switch to
    "Open my dashboard" rather than sign-up prompts, so it stays one click
    from work instead of being a dead end.
    """
    return templates.TemplateResponse("landing.html", _ctx(request, user))


@router.get("/login")
def login_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("login.html", _ctx(request, None))


@router.get("/register")
def register_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("register.html", _ctx(request, None))


@router.get("/dashboard")
def dashboard_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("dashboard.html", _ctx(request, user))


@router.get("/work")
def work_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("work.html", _ctx(request, user))


@router.get("/work/communities")
def work_communities_page(
    request: Request, user: User | None = Depends(get_current_user_optional)
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("work_communities.html", _ctx(request, user))


@router.get("/work/tasks")
def work_tasks_page(
    request: Request, user: User | None = Depends(get_current_user_optional)
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("work_tasks.html", _ctx(request, user))


# A task and a community each get a real URL rather than a dialog over the
# dashboard. That is what makes the back button, the Android back gesture and
# a notification deep link all mean the same thing.
#
# No ownership check here: the page is a shell, and the data behind it comes
# from the API, which does check. Guessing an id gets you an empty frame and a
# 403 in the fetch, not somebody else's task.
@router.get("/work/task/{task_id}")
def work_task_page(
    task_id: str, request: Request, user: User | None = Depends(get_current_user_optional)
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "work_detail.html",
        _ctx(request, user, kind="task", oid=task_id,
             body_id="wk-taskdetail-body", page_title="Task"),
    )


@router.get("/work/community/{community_id}")
def work_community_page(
    community_id: str, request: Request, user: User | None = Depends(get_current_user_optional)
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "work_detail.html",
        _ctx(request, user, kind="community", oid=community_id,
             body_id="wk-detail-body", page_title="Community"),
    )


# The work board: who is carrying what, and what each of them has done.
# A page of its own rather than a panel on the community, because it is a
# different question -- the community page is about membership, this is about
# workload -- and because a URL makes it linkable from a notification.
@router.get("/work/community/{community_id}/board")
def work_board_page(
    community_id: str, request: Request, user: User | None = Depends(get_current_user_optional)
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "work_board.html",
        _ctx(request, user, community_id=community_id, page_title="Work board"),
    )


@router.get("/timetable")
def timetable_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("timetable.html", _ctx(request, user))


@router.get("/calendar")
def calendar_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("calendar.html", _ctx(request, user))


@router.get("/reminders")
def reminders_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("reminders.html", _ctx(request, user))


@router.get("/tasks")
def tasks_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("tasks.html", _ctx(request, user))


@router.get("/admin")
def admin_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    if not admin_scope.is_panel_admin(user):
        # Non-admins get sent home rather than shown a page they can't use;
        # the API behind it enforces the real 403. A college administrator is
        # let through to the same page -- what differs is what the endpoints
        # behind it will return, not which template renders.
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("admin.html", _ctx(request, user))


# Managing people is a place you go, not a question you answer: a filtered
# table with an edit form, a password reset and a delete behind every row.
# That was a panel on the admin page and is now a page of its own, reached
# from the Members button on a college -- so the college you are looking at is
# in the URL, the browser's back button works, and a filtered list can be
# linked to rather than re-found.
@router.get("/admin/members")
def admin_members_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    if not admin_scope.is_panel_admin(user):
        return RedirectResponse(url="/dashboard")
    # The shell only; every row behind it comes from the API, which scopes.
    return templates.TemplateResponse("admin_members.html", _ctx(request, user))


@router.get("/profile")
def profile_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("profile.html", _ctx(request, user))
