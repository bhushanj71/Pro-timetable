# Deploying on Vercel

The app runs on Vercel as a Python serverless function, and on Render as a
long-running process, from the same code. Nothing is forked; the differences
are detected at runtime from `VERCEL=1`, which the platform sets itself.

---

## First, what is actually slow

Worth measuring before moving, because moving hosts fixes some of these and
none of the others. From the live Render deployment:

| Request | Time to first byte |
|---|---|
| `/` (page render, little database work) | 0.32 s |
| `/api/health` (one `SELECT 1`) | 0.80 – 1.25 s |
| `/static/css/style.css` (68 KB) | 0.35 s, 0.58 s complete |

The landing page is fine. The health check does almost nothing except talk to
the database and takes three times as long, which points at **round-trip
latency between the app and Supabase**, not at the host.

That matters, because:

- **Moving to Vercel does not fix it.** Put the Vercel deployment in a region
  near your Supabase project or you will have moved the problem. Supabase tells
  you its region; Vercel's is set per project.
- **Static assets do get faster.** On Vercel they are served from the CDN
  rather than through Python. That 0.58 s becomes a cache hit.
- **Cold starts get much faster**, both from Vercel's shorter start and from
  the startup change below.

---

## What changed to make serverless work

**Startup went from 33 database round trips to 1.** `create_all` plus a
reflection of every table costs the same 33 statements whether or not anything
needs doing. A long-running host pays that once at boot; a serverless one pays
it *on every cold start*, which at these round-trip times is seconds on
somebody's first request. The schema the build expects is now fingerprinted and
the fingerprint stored, so a database that is already correct is confirmed with
one query. Any model change or new migration entry moves the fingerprint and
the full pass runs again.

**Background threads do not start on serverless.** A thread there is frozen
between invocations and holds a database connection while it sleeps, so it is
worse than useless. Two things used them:

- *Reminder delivery* now comes from Vercel Cron hitting
  `/api/cron/process-reminders`, which is a real request and therefore actually
  runs. Already configured in `vercel.json`.
- *Database replication* is driven from requests instead, as a background task
  attached to the response — so the visitor has their reply before any copying
  begins. This fits serverless better than it sounds: no traffic means no
  writes, so a mirror that only advances while requests arrive is never behind
  on anything. An idle app has nothing to copy.

---

## Setting it up

1. Import the repository on Vercel. It reads `vercel.json`; there is nothing to
   configure in the UI about the build.
2. Set the environment variables — the same ones Render has:

   ```
   DATABASE_URL          the Supabase pooler URL
   SECRET_KEY
   CRON_SECRET           so the cron endpoint is not open
   AI_PROVIDER / AI_API_KEY / AI_MODEL
   VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY
   MIRROR_DATABASE_URL   optional, see docs/database-mirror.md
   ```

3. Set the project's region to whichever is closest to your Supabase project.
   This is the single biggest thing you can do about the latency above.

Use the **pooler** host (`aws-<region>.pooler.supabase.com`), never the direct
`db.<ref>.supabase.co` one. The direct host is IPv6-only, and it is also the
wrong shape for serverless: every cold start opens its own connection, and a
pooler is what keeps that from exhausting the database's connection limit.

---

## Running both at once

Both platforms can point at the same database and both will work. That is
useful for comparing them, with one caveat: **turn database failover off** if
two deployments are live at the same time.

```
REPLICATION_ALLOW_FAILOVER=false
```

Two independent deployments that each decided to fail over would write to
different databases and diverge. Replication keeps running; neither deployment
switches on its own.

---

## What still only works on Render

Nothing the application needs. But two things are worth knowing:

- **Vercel Hobby crons run once a day.** Reminder delivery is scheduled at
  01:00 and that is the fastest the free tier allows. On Render the in-process
  scheduler can run continuously (`ENABLE_BACKGROUND_SCHEDULER=true`). If
  reminders need to be timely, that is an argument for keeping Render, or for
  Vercel Pro.
- **Function execution time is capped** (10 s on Hobby). Every endpoint here is
  far below that, but a very large attachment upload on a slow connection is
  the one to watch.
