"""
Server-rendered HTML pages (Jinja2). All data is fetched client-side from
the JSON API via fetch/HTMX so these views stay thin.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import terms as terms_policy
from app.deps import get_current_user_optional
from app.models import User
from app.services import admin_scope

# Pages a professor can still reach while they owe an answer about the terms.
# The terms themselves, obviously -- being unable to read what you are being
# asked to agree to would be a closed loop -- and the ways in and out.
TERMS_EXEMPT = frozenset({
    "/", "/login", "/register", "/terms", "/terms/accept",
    "/robots.txt", "/sitemap.xml",
})


def terms_gate(request: Request,
               user: User | None = Depends(get_current_user_optional)) -> None:
    """Send anyone who has not agreed to the terms to the page that asks.

    A dependency on the router rather than a line in each of fifteen handlers,
    because the failure mode of the second is a page somebody forgets to add
    it to -- and a consent gate with one way round it is decoration.

    It costs nothing: FastAPI caches a dependency's result within a request,
    so the user resolved here is the same object the handler already asked
    for, not a second query.
    """
    if request.url.path in TERMS_EXEMPT:
        return
    if terms_policy.needs_acceptance(user):
        raise HTTPException(
            status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/terms/accept"},
        )


router = APIRouter(tags=["pages"], dependencies=[Depends(terms_gate)])
# Absolute path so the templates resolve regardless of working directory.
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _site_url(request: Request) -> str:
    """The address this site is canonically at.

    Derived rather than written into the templates: the canonical host, the
    configured base URL and whatever the request arrived on are three answers
    to the same question, and a page that hard-codes one of them is wrong on
    localhost or wrong in production.
    """
    from app.config import get_settings

    settings = get_settings()
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.rstrip("/")
    if settings.CANONICAL_HOST:
        return f"https://{settings.CANONICAL_HOST}"
    return str(request.base_url).rstrip("/")


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
        "site_url": _site_url(request),
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


# What is behind one of the dashboard's three counts. A page rather than a
# dialog, so the bucket lives in the URL and the back button means something.
#
# The bucket is validated here as well as in the API. A shell that renders for
# any word in the path would put a heading and a spinner on screen before the
# fetch behind it 404s, which is a worse way to say "no such thing" than not
# opening the page.
WORK_HISTORY = {
    "active": ("Active work", "📈", "active"),
    "pending": ("Waiting on your answer", "⏱️", "info"),
    "completed": ("Completed work", "✅", "lab"),
}


@router.get("/work/history/{bucket}")
def work_history_page(
    bucket: str, request: Request, user: User | None = Depends(get_current_user_optional)
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    if bucket not in WORK_HISTORY:
        return RedirectResponse("/work", status_code=302)
    title, icon, accent = WORK_HISTORY[bucket]
    return templates.TemplateResponse(
        "work_history.html",
        _ctx(request, user, bucket=bucket, page_title=title, icon=icon, accent=accent),
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


# ---------------------------------------------------------------------------
# The terms, and the one place they are asked for
# ---------------------------------------------------------------------------
@router.get("/terms")
def terms_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    """Public on purpose: terms you need an account to read are not terms you
    can agree to before making one."""
    return templates.TemplateResponse(
        "terms.html",
        _ctx(request, user,
             terms_version=terms_policy.TERMS_VERSION,
             # Not the referer. That is attacker-controlled and this value is
             # rendered as a link; "back" is worth less than an open redirect
             # costs.
             back_to="/dashboard" if user else "/"),
    )


@router.get("/terms/accept")
def terms_accept_page(request: Request,
                      user: User | None = Depends(get_current_user_optional)):
    """Asked only of accounts that never saw the sign-up form -- Google
    sign-in creates one from the sign-in page."""
    if not user:
        return RedirectResponse(url="/login")
    if not terms_policy.needs_acceptance(user):
        # Already answered. Coming back to the question would suggest the
        # answer had not been recorded.
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        "terms_accept.html",
        _ctx(request, user, terms_version=terms_policy.TERMS_VERSION),
    )


@router.get("/profile")
def profile_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("profile.html", _ctx(request, user))


# ---------------------------------------------------------------------------
# What a crawler is told
#
# Only the public pages. Everything behind the login is disallowed -- not as a
# security measure, since the API enforces that and a robots file is a request
# rather than a rule, but because a crawler following /dashboard gets a login
# redirect and indexes that instead of the page it was after.
# ---------------------------------------------------------------------------
@router.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    site = _site_url(request)
    body = "\n".join([
        "User-agent: *",
        "Allow: /$",
        "Allow: /login",
        "Allow: /register",
        "Allow: /terms",
        "Disallow: /api/",
        "Disallow: /dashboard",
        "Disallow: /timetable",
        "Disallow: /calendar",
        "Disallow: /tasks",
        "Disallow: /reminders",
        "Disallow: /work",
        "Disallow: /admin",
        "Disallow: /profile",
        "",
        f"Sitemap: {site}/sitemap.xml",
        "",
    ])
    return Response(body, media_type="text/plain")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    site = _site_url(request)
    urls = "".join(
        f"<url><loc>{site}{path}</loc><changefreq>weekly</changefreq>"
        f"<priority>{priority}</priority></url>"
        for path, priority in (("/", "1.0"), ("/login", "0.5"), ("/register", "0.8"),
                              ("/terms", "0.3"))
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f"{urls}</urlset>")
    return Response(xml, media_type="application/xml")
