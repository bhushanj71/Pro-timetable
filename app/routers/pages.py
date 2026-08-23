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
        **extra,
    }


@router.get("/")
def index(request: Request, user: User | None = Depends(get_current_user_optional)):
    # Signed-in professors go straight to work; everyone else gets the pitch.
    if user:
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("landing.html", _ctx(request, None))


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
    if not user.is_admin:
        # Non-admins get sent home rather than shown a page they can't use;
        # the API behind it enforces the real 403.
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("admin.html", _ctx(request, user))


@router.get("/profile")
def profile_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("profile.html", _ctx(request, user))
