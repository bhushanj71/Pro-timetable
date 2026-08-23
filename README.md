# ProfSchedule AI

An AI-powered personal timetable, task, and reminder system built for college professors. Describe your schedule in plain English — "I teach ANN every Monday, Wednesday and Friday from 10 to 11 AM" — and ProfSchedule AI turns it into a structured, conflict-checked weekly timetable with reminders.

## Features

- **Natural-language scheduling** — a single AI prompt box creates events, reminders, and tasks; the assistant always shows what it understood before writing anything to the database.
- **AI timetable generator** — hand it subjects, lecture counts, working hours, and a lunch break; it places lectures automatically and flags anything it couldn't fit.
- **Conflict detection** — overlapping events are blocked by default, with a "next free slot" suggestion and a force-create override.
- **Free-time finder** — "When am I free this Friday?" / "Find me a 1-hour slot tomorrow."
- **Weekly timetable, calendar (day/week/month/agenda), tasks, and a notification center** with in-app + email reminders.
- **Recurring events** (weekly on any combination of days, daily, or monthly-by-weekday) materialized as individual events so editing, conflict checks, and calendar rendering stay simple.
- **Export** the week as CSV, ICS, or PDF; **import** events from CSV.
- **Provider-agnostic AI** — works with OpenAI, NVIDIA NIM, Ollama, or any OpenAI-compatible endpoint, selected entirely via environment variables. Falls back to a built-in rule-based parser if no AI key is configured, so the app stays usable without one.
- **JWT auth in an httpOnly cookie**, per-user data isolation, no API keys ever touch the frontend.

## Architecture

```
Browser (Jinja2 + vanilla JS + Alpine/HTMX)
        │  fetch() / cookie auth
        ▼
FastAPI app (app/main.py)
  ├─ routers/        HTTP endpoints (auth, events, tasks, reminders, timetable, ai, export, analytics, cron, pages)
  ├─ services/        business logic (ai_service, scheduler, conflict_service, reminder_service, recurrence, nlp_dates)
  ├─ models.py        SQLAlchemy ORM (User, Event, Reminder, Task, AIConversation)
  └─ schemas.py        Pydantic request/response + the AI structured-output contract
        │
        ▼
SQLite (local dev) or Postgres (production, e.g. Vercel Postgres / Neon / Supabase)
```

Deployed on Vercel as a single Python serverless function (`api/index.py` exports the FastAPI `app`); `vercel.json` routes all traffic to it and schedules a cron hit against `/api/cron/process-reminders` to deliver due reminders, since Vercel functions don't run persistent background workers.

### AI flow

```
prompt → AIService.process_prompt() → AIExtractionResult (Pydantic-validated)
       → conflict check → "here's what I understood" shown to the professor
       → POST /api/ai/confirm → database write → reminders scheduled
```

The AI never writes to the database directly — every response is validated against `AIExtractionResult` first, and nothing is persisted until the professor confirms.

## Project structure

```
prof-schedule-ai/
├── api/index.py            Vercel serverless entrypoint (exports `app`)
├── app/
│   ├── main.py              FastAPI app factory
│   ├── config.py            Settings (env vars)
│   ├── database.py          SQLAlchemy engine/session
│   ├── models.py            ORM models
│   ├── schemas.py           Pydantic schemas + AI contracts
│   ├── security.py          Password hashing, JWT
│   ├── deps.py               get_current_user, get_db
│   ├── routers/              auth, events, tasks, reminders, timetable, ai, export, analytics, cron, pages
│   ├── services/             ai_service, scheduler, conflict_service, reminder_service, recurrence, nlp_dates
│   ├── templates/             Jinja2 pages (dashboard, calendar, timetable, reminders, tasks, profile, auth)
│   └── static/{css,js}        Stylesheet + per-page vanilla JS
├── tests/                     pytest suite (auth, events, AI extraction, reminders)
├── requirements.txt
├── requirements-dev.txt
├── vercel.json
├── .env.example
└── package.json
```

## Administrator access

There is deliberately **no self-service way to become an admin** — the first
one is created from the command line:

```bash
python create_admin.py
```

It will prompt for an email, name, and password (read via `getpass`, so it
never lands in your shell history). Passing an email that already exists
promotes that account instead of creating a new one.

Once signed in as an admin, an **Admin** link appears in the sidebar
(`/admin`), giving you:

- System-wide stats: users, events, tasks, reminders, AI prompt volume
- Full user management: create, edit, deactivate, delete
- Promote/demote administrators
- Reset any user's password
- Search users by name or email

Safety rails enforced server-side: you cannot delete or deactivate your own
account, and the system refuses to remove the **last** remaining
administrator.

Every `/api/admin/*` route is gated by a dedicated `get_current_admin`
dependency that returns 403 for regular professors, and deactivated accounts
are refused at login.

### Supabase setup

1. Create a project at [supabase.com](https://supabase.com).
2. Go to **Project Settings → Database** and copy the connection string plus
   your database password.
3. Set it in `.env` (or in Vercel's environment variables):

   ```env
   DATABASE_URL=postgresql://postgres:YOUR-PASSWORD@db.<project-ref>.supabase.co:5432/postgres
   ```

4. Start the app once — tables are created automatically, and any columns
   added by a later upgrade are backfilled in place.
5. Run `python create_admin.py` to mint your administrator account.

> Supabase also offers a **connection pooler** URL (port `6543`). Prefer that
> one for serverless/Vercel deployments, since each function invocation opens
> its own connection and the direct port-`5432` endpoint exhausts quickly.

## Local development

Requires Python 3.11+ (tested against 3.10+ as well).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements-dev.txt
cp .env.example .env          # then edit .env — SECRET_KEY at minimum
uvicorn app.main:app --reload
```

Open http://localhost:8000 — it redirects to `/login`. Register a professor account, and you're on the dashboard.

### Run the tests

```bash
pytest tests/ -v
```

The suite uses an isolated in-memory SQLite database and exercises the rule-based AI fallback (no API key required to test).

## Environment variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | JWT signing secret — set a long random value in production |
| `DATABASE_URL` | `sqlite:///./profschedule.db` locally, or a Postgres URL in production |
| `AI_PROVIDER` | `openai` \| `nvidia` \| `ollama` \| `none` |
| `AI_API_KEY` | API key for the chosen provider (leave blank to use the rule-based fallback) |
| `AI_MODEL` | Model name, e.g. `gpt-4o-mini` |
| `AI_BASE_URL` | Override for any other OpenAI-compatible endpoint (vLLM, LM Studio, Groq, …) |
| `CRON_SECRET` | Shared secret checked on `/api/cron/process-reminders` |
| `SMTP_*` | Optional, for email reminder delivery |

See `.env.example` for the full list.

## Database setup

- **Local dev**: SQLite works out of the box, no setup needed. Tables are created automatically on startup (`init_db()`).
- **Production**: point `DATABASE_URL` at a Postgres instance (Vercel Postgres, Neon, Supabase, etc.). The app normalizes `postgres://` / `postgresql://` URLs to the `psycopg` 3 driver automatically. Tables are created on cold start — for a real production rollout, switch to Alembic migrations instead of relying on `create_all`.

> **Note on timezones**: events are always stored as UTC-aware datetimes, converted from the professor's configured timezone at write time. This keeps chronological ordering correct on both SQLite (which drops tzinfo on read-back) and Postgres (which preserves it).

## AI provider setup

No code changes are needed to switch providers — set three environment variables:

```env
# OpenAI
AI_PROVIDER=openai
AI_API_KEY=sk-...
AI_MODEL=gpt-4o-mini

# NVIDIA NIM
AI_PROVIDER=nvidia
AI_API_KEY=nvapi-...
AI_MODEL=meta/llama-3.1-8b-instruct

# Any other OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, Groq...)
AI_PROVIDER=ollama
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=llama3.1
```

If `AI_API_KEY` is empty (or the call fails), the app falls back to a built-in rule-based extractor so scheduling still works, with reduced accuracy on ambiguous prompts.

## Phone notifications

Reminders reach a professor's phone through three independent channels. Each
is optional and toggled per user in **Profile → Phone Notifications**.

> A server cannot push to a phone merely because the user is signed into that
> email on the device — email delivery and push are separate channels. The
> options below are the three that genuinely work.

### 1. Email — to the address they signed in with

Set the SMTP variables and reminders arrive in their inbox; the phone's mail
app raises its own notification. Nothing for the professor to install.

With Gmail, create an **App Password** (Google Account → Security →
2-Step Verification → App passwords) and use that as `SMTP_PASSWORD`. A
normal account password will be rejected.

### 2. Web Push — lock-screen notifications

True push, delivered even when the site is closed. Generate a keypair once:

```bash
python generate_vapid_keys.py
```

Set the printed `VAPID_*` values in the environment. The professor then opens
**Profile → Enable on this device** and accepts the browser prompt.

- **Android / Chrome / desktop** — works immediately.
- **iPhone** — iOS only exposes push to installed web apps. They must tap
  **Share → Add to Home Screen** and open the app from that icon first
  (iOS 16.4+). The profile page says so when it detects iOS.

Requires HTTPS, which Render provides.

### 3. Calendar subscription — native reminders, no app

Profile shows a personal `.ics` feed URL. Adding it in Google Calendar
(**Other calendars → From URL**) or iPhone (**Settings → Calendar → Accounts
→ Add Subscribed Calendar**) makes the phone's own calendar fire native
reminders. Each event carries a `VALARM` set to the professor's default
reminder lead time.

The token in the URL *is* the credential, so treat it like a password. It can
be rotated from the profile page, which immediately invalidates the old link.

### 4. Google Sign-In and Google Calendar sync

The most reliable route to a phone notification: write the class into the
professor's own Google Calendar and let Google deliver the alert. No push
keys, no SMTP, and it works with the Calendar app they already use.

Professors can also create an account with one tap instead of a password.

**Google Cloud Console setup**

Google renamed this area to **Google Auth Platform** in 2025 — there is no
longer an "OAuth consent screen" page.

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com).
2. **APIs & Services → Library** → enable **Google Calendar API**.
3. **Google Auth Platform → Branding** — set the app name, user support email
   and developer contact.
4. **Google Auth Platform → Audience** — choose the user type:

   | | Internal | External |
   |---|---|---|
   | Who can sign in | Anyone in your Workspace domain | Any Google account |
   | "Unverified app" warning | None | Shown until verified |
   | User cap | Unlimited | 100 test users |
   | Verification | Never required | Required, and slow for the calendar scope |

   **Internal is strongly preferred for a college deployment** — every
   professor already has a domain account, and it avoids Google verification
   entirely. It only appears when you are signed in with a Google Workspace
   account; a personal Gmail can only create External apps.

   If you pick External, add yourself under *Test users*, or sign-in will be
   refused.

5. **Google Auth Platform → Data Access** → *Add or remove scopes*:
   `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`, and
   `https://www.googleapis.com/auth/calendar.events`.
6. **Google Auth Platform → Clients** → *Create client* → **Web application**.
   Add the authorised redirect URIs:

   ```
   https://your-app.onrender.com/auth/google/callback
   http://localhost:8000/auth/google/callback
   ```

7. Set the printed values in the environment:

   ```env
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=...
   PUBLIC_BASE_URL=https://your-app.onrender.com
   ```

The **Continue with Google** button and the Calendar card appear on their own
once these are set, and stay hidden otherwise, so a missing config never
shows a dead link.

**How it behaves**

- Signing in with Google creates the account if it's new, or links to an
  existing one with the same email — a professor who registered with a
  password can then use either method.
- Connecting Calendar mirrors every create, edit and delete into their Google
  Calendar, with popup reminders at their configured lead time plus 5 minutes.
- **Profile → Sync upcoming events now** backfills existing classes.
- Refresh tokens are encrypted at rest with a key derived from `SECRET_KEY`,
  so a leaked database row does not hand over calendar access.
- Disconnecting is refused for accounts that have no password, since it would
  lock the professor out.

> **Only relevant if you chose External:** while the app is in Testing mode
> Google shows an "unverified app" warning and only listed test users can sign
> in (up to 100). Removing that means submitting for verification, and because
> `calendar.events` is a sensitive scope that requires a privacy policy and a
> verified domain, with a review that takes weeks. Choosing **Internal** avoids
> all of this.

### Keeping delivery on time

Reminders only fire when the server is awake. Render's free tier sleeps after
~15 minutes idle, so the in-process scheduler stops.

`.github/workflows/reminders.yml` pings `/api/cron/process-reminders` every
10 minutes, which both delivers due reminders and keeps the instance awake.
Add a repository secret `CRON_SECRET` matching the server's `CRON_SECRET`
value, and update the URL in that workflow to your own deployment.

Delivery is idempotent (`is_sent`), so the cron ping and the in-process
scheduler can both run without double-sending. Failed sends retry on later
ticks up to three times before being retired.

## Deploying to Render

The repo ships a `render.yaml` blueprint, so the fastest path is:

1. In Render: **New → Blueprint**, point it at this repo, and approve the plan.
2. Set the secrets Render deliberately does not read from git
   (**Environment** tab):

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | Your Supabase/Postgres URL (see below) |
   | `AI_PROVIDER` | `nvidia` (or `openai`) |
   | `AI_API_KEY` | Your provider API key |
   | `AI_MODEL` | e.g. `meta/llama-3.1-8b-instruct` |

3. Deploy, then create your admin account from the Render **Shell** tab:

   ```bash
   python create_admin.py
   ```

`SECRET_KEY` is generated by Render once and held stable across deploys, so
logins survive a redeploy.

### Or configure it manually

If you'd rather create the service by hand instead of using the blueprint:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Health check path:** `/api/health`

Binding `0.0.0.0` and Render's `$PORT` is required — binding `127.0.0.1` or a
hard-coded port makes the health check fail and the deploy hang.

### Reminders on Render

Unlike Vercel, Render runs a **persistent process**, so reminders are
delivered by an in-process loop rather than an external cron ping. The
blueprint enables it:

```env
ENABLE_BACKGROUND_SCHEDULER=true
REMINDER_POLL_SECONDS=60
```

Delivery is idempotent (guarded by `is_sent`), so the
`/api/cron/process-reminders` endpoint remains available and safe to call
alongside it.

> **Two caveats on Render's free tier**
>
> 1. Free web services **sleep after ~15 minutes of inactivity**. While
>    asleep the scheduler isn't running, so reminders fire late — they are
>    delivered on the next wake, not at their scheduled minute. Use a paid
>    instance (or an external uptime ping) if on-time reminders matter.
> 2. The free disk is **ephemeral** — do not use SQLite in production there
>    or you will lose all data on every redeploy. Point `DATABASE_URL` at
>    Supabase or Render Postgres.

## Vercel deployment

1. Push this repo to GitHub and import it in Vercel.
2. Set the environment variables above in the Vercel project settings (at minimum `SECRET_KEY`, `DATABASE_URL`, and your AI provider config).
3. Provision a Postgres database (Vercel Postgres, Neon, or Supabase) and set `DATABASE_URL`.
4. Deploy — `vercel.json` builds `api/index.py` with `@vercel/python` and routes all paths to it.

### Reminder scheduling on Vercel

`vercel.json` includes a cron entry hitting `/api/cron/process-reminders` every 5 minutes:

```json
"crons": [{ "path": "/api/cron/process-reminders", "schedule": "*/5 * * * *" }]
```

**Vercel's Hobby plan only allows once-daily cron jobs.** On Hobby, either upgrade to Pro for higher-frequency crons, or point an external scheduler (cron-job.org, GitHub Actions on a schedule, etc.) at the same endpoint with the `Authorization: Bearer <CRON_SECRET>` header. Delivery is idempotent (`is_sent`/`sent_at`/`retry_count`), so calling it more often than necessary is harmless.

## API overview

All endpoints under `/api/*` return JSON and require the `access_token` cookie (set on login/register) except `/api/auth/register`, `/api/auth/login`, and `/api/cron/*` (secret-protected).

- **Auth**: `POST /api/auth/{register,login,logout,token}`, `GET/PUT /api/auth/me`
- **Events**: `GET/POST /api/events`, `GET/PUT/DELETE /api/events/{id}`, `POST /api/events/{id}/duplicate`
- **Tasks**: `GET/POST /api/tasks`, `PUT/DELETE /api/tasks/{id}`, `POST /api/tasks/{id}/complete`
- **Reminders**: `GET/POST /api/reminders`, `DELETE /api/reminders/{id}`, `GET /api/reminders/notifications`
- **Timetable**: `POST /api/timetable/generate[?commit=true]`, `GET /api/timetable`
- **AI**: `POST /api/ai/process-prompt`, `POST /api/ai/confirm`, `POST /api/ai/{generate-timetable,find-free-time,resolve-conflict,query-schedule}`
- **Export**: `GET /api/export/{csv,ics,pdf}`, `POST /api/export/import/csv`
- **Analytics/search**: `GET /api/analytics`, `GET /api/search?q=`

Interactive docs are available at `/docs` (Swagger) and `/redoc` when running locally.

## Security notes

- Passwords are hashed with bcrypt (via passlib); JWTs are signed with `SECRET_KEY` and stored in an httpOnly, `SameSite=Lax` cookie.
- The AI service never executes model output directly — everything is parsed into Pydantic models (`AIExtractionResult`) before it can touch the database, and event/reminder/task creation always requires an explicit confirm step.
- All unhandled exceptions are caught and return a generic message — no stack traces, database errors, or API keys are ever exposed to the client.
- Every query is scoped to `user_id`, so professors can only see and modify their own data.
