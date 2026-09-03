# The mirror database

A second database kept in step with the first, and switched to automatically if
the first stops answering.

**It is off until you configure it.** With `MIRROR_DATABASE_URL` unset there is
no second engine, no worker, and not one extra write on any transaction.

---

## What you need to provision

A **separate managed Postgres** with its own URL. Free tiers that work:

| Provider | Note |
|---|---|
| Supabase | A second project, in a different region from the primary |
| Neon | Free tier, separate project |
| Render Postgres | Free instance |

Then set two environment variables **on the Render service** — Dashboard →
your service → Environment → Add Environment Variable. Saving them restarts the
app, which is when the mirror is picked up.

```
MIRROR_DATABASE_URL=<the real connection string from your provider>
REPLICATION_ALLOW_FAILOVER=true
```

The first value is a real URL copied out of the provider's dashboard, of the
shape `postgresql://USER:PASSWORD@HOST:5432/DATABASE`. Pasting that shape
literally is caught and reported on `/api/health` rather than being retried
against a host called "host":

```json
"last_error": "MIRROR_DATABASE_URL still contains the example text 'user:pass@host'. …"
```

Nothing else. On the next start the mirror is given the same schema, filled with
everything the primary already holds, and kept in step from then on.

### Put it somewhere else

A mirror in the same region, on the same provider, under the same account is a
copy — not redundancy. It survives a dropped table; it does not survive the
outage you actually want to survive. Different provider, or at least a different
region.

### What will not work

A local SQLite file. On a host with an ephemeral disk — Render included — it
disappears on the next deploy, which is precisely when a backup is wanted. The
app will let you set it and it will appear to work, which is worse than
refusing.

---

## What it does

**Replication.** Every committed change appends one row to `replication_log`
inside the same transaction: the table, the primary key, and whether the row was
written or deleted. A background worker reads that log, fetches each row's
current state from the source, and writes it to the destination. Typical lag is
about a second.

Recording *identity* rather than *values* is the decision everything else
follows from:

- The log stays small. A 10 MB attachment does not also become a 13 MB log row.
- Replaying is harmless, so a crash mid-copy costs nothing.
- A row edited three times before the worker catches up is copied once, at its
  final value.
- A row deleted after being logged simply is not there when the worker looks,
  and the deletion propagates. The two databases agree either way.

**Failover.** Three consecutive connection failures on real traffic — not a
timer — switch the application to the mirror. One timeout is a blip; flapping
between two databases is worse than a moment of slowness on one. A successful
request resets the count.

**Failback.** Once the primary answers again, everything written to the mirror
during the outage is replayed to it *first*, and only then does the app switch
back. Switching back early would serve a database missing every write made
during the outage, which is a worse failure than the outage was.

---

## What it does not do

**It is asynchronous.** A failover can lose the last second or so of writes.
Synchronous replication with guaranteed zero loss is a database-level feature —
Postgres streaming replication with a coordinator such as Patroni, or a managed
high-availability plan — and no application-level design can honestly claim it.
If losing a second of writes is unacceptable, this is the wrong tool and a
managed HA plan is the right one.

**It assumes one writing process.** Two app instances that disagreed about which
database is live would write to different ones and diverge, and untangling that
needs a coordinator holding a lease. If you ever scale past one instance, set
`REPLICATION_ALLOW_FAILOVER=false` — replication keeps running, the app just
never switches on its own.

**It is not a substitute for backups.** A mirror replicates mistakes perfectly.
`DROP TABLE` on the primary is `DROP TABLE` on the mirror a second later. Keep
your provider's point-in-time recovery switched on; that is what protects you
from the damage a mirror faithfully copies.

---

## Checking on it

`/api/health` reports the state:

```json
"replication": {
  "enabled": true,
  "serving_from": "primary",
  "failed_over": false,
  "failed_over_at": null,
  "last_replicated_at": "2026-09-03T16:18:46Z",
  "pending_changes": 0,
  "last_error": null,
  "failover_allowed": true
}
```

- `serving_from` — which database is answering right now.
- `pending_changes` — how far behind the mirror is. Steady near zero. A number
  that climbs means the mirror is unreachable or slow, and is the thing worth
  alerting on.
- `failed_over_at` — set while running on the mirror.

A failover writes an ERROR line to the log naming both databases and the reason.

---

## Testing it for real

Do this once, deliberately, before you need it:

1. Confirm `pending_changes` is 0 and `serving_from` is `primary`.
2. Break the primary's credentials, or pause the database at the provider.
3. Use the app. It should keep working; `/api/health` should show
   `"serving_from": "mirror"`.
4. Restore the primary. Within about fifteen seconds `serving_from` returns to
   `primary`, and anything written during the outage is there.

A failover mechanism nobody has ever exercised is a guess.
