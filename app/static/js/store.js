/* Client data layer: caching, request de-duplication, invalidation.

   Measured before writing this: one dashboard load fired 11 API requests,
   with /api/events hit four times and /api/tasks and /api/onboarding/status
   requested twice each with identical URLs. Nothing was cached, so returning
   to a page re-fetched everything and the interface sat empty while it did.

   Three mechanisms, deliberately small and framework-free -- this app is
   vanilla JS with no bundler, so a data-fetching library would be a bigger
   dependency than the problem:

     dedupe   two callers asking for the same URL in the same tick share one
              request rather than racing each other
     cache    a fresh entry is returned immediately, with no network at all
     revalidate  a stale entry is returned immediately AND refreshed behind
              the scenes, so the page paints instantly and corrects itself

   Freshness is per-resource because the risk differs: a profile changes
   rarely, whereas showing yesterday's timetable is the one thing this app
   must never do. */

const CACHE_TTL = {
  "/api/auth/me": 5 * 60_000,
  "/api/onboarding/status": 5 * 60_000,
  "/api/push/devices": 60_000,
  "/api/analytics": 60_000,
  "/api/events": 30_000,
  "/api/timetable": 30_000,
  "/api/tasks": 30_000,
  "/api/reminders": 30_000,
  "/api/work/dashboard": 20_000,
  "/api/work/communities": 60_000,
  "/api/work/invitations": 20_000,
  "/api/work/tasks": 20_000,
  // Never served from cache: the bell is the one thing that must be current,
  // and it is cheap.
  "/api/reminders/notifications": 0,
};

const DEFAULT_TTL = 20_000;

const cache = new Map();     // url -> {data, at, ttl}
const inflight = new Map();  // url -> Promise

const stats = { hits: 0, misses: 0, deduped: 0, revalidations: 0 };

function ttlFor(url) {
  const path = url.split("?")[0];
  return CACHE_TTL[path] ?? DEFAULT_TTL;
}

const isFresh = (entry) => entry && Date.now() - entry.at < entry.ttl;

/* A stale entry is still worth showing. Beyond this it is not: coming back to
   a tab after an hour should not flash yesterday's schedule before correcting
   itself. */
const STALE_LIMIT = 10 * 60_000;
const isUsable = (entry) => entry && Date.now() - entry.at < entry.ttl + STALE_LIMIT;

function fetchOnce(url) {
  // Same URL already in flight: hand back the same promise rather than
  // opening a second connection for an identical answer.
  if (inflight.has(url)) {
    stats.deduped++;
    return inflight.get(url);
  }
  const p = apiFetch(url)
    .then((data) => {
      cache.set(url, { data, at: Date.now(), ttl: ttlFor(url) });
      return data;
    })
    .finally(() => inflight.delete(url));

  inflight.set(url, p);
  return p;
}

/**
 * Read a URL through the cache.
 *
 * @param {string} url
 * @param {object} [opts]
 * @param {boolean} [opts.force]  bypass the cache entirely
 * @param {(data:any)=>void} [opts.onRevalidated]  called if a background
 *        refresh returns data different from what was served
 */
async function cachedFetch(url, { force = false, onRevalidated } = {}) {
  const entry = cache.get(url);

  if (!force && isFresh(entry)) {
    stats.hits++;
    return entry.data;
  }

  if (!force && isUsable(entry)) {
    // Stale-while-revalidate: paint now, correct in a moment.
    stats.hits++;
    stats.revalidations++;
    fetchOnce(url)
      .then((fresh) => {
        if (onRevalidated && JSON.stringify(fresh) !== JSON.stringify(entry.data)) {
          onRevalidated(fresh);
        }
      })
      .catch(() => {
        /* A failed background refresh leaves the shown data alone. */
      });
    return entry.data;
  }

  stats.misses++;
  if (force) cache.delete(url);
  return fetchOnce(url);
}

/**
 * Drop cached entries. Called after every write, because the alternative --
 * a stale timetable after creating a lecture -- is the specific failure this
 * cache must never cause.
 *
 * @param {...(string|RegExp)} patterns  matched against the cached URL
 */
function invalidate(...patterns) {
  for (const key of [...cache.keys()]) {
    if (patterns.some((p) => (p instanceof RegExp ? p.test(key) : key.startsWith(p)))) {
      cache.delete(key);
    }
  }
}

/** Everything a schedule write can affect. */
function invalidateSchedule() {
  invalidate("/api/events", "/api/timetable", "/api/analytics", "/api/reminders");
}

/** Wipe on sign-out, so the next account never sees the previous one's data. */
function clearCache() {
  cache.clear();
  inflight.clear();
}

/* Personal and Work are separate datasets that happen to share a cache.
   Invalidating one must not disturb the other: a professor creating a lecture
   should not cost their Work dashboard a refetch, and more importantly a
   stale Work entry must never be able to answer a Personal read. The URL
   prefixes keep them disjoint -- /api/work/* against everything else -- so
   this is a matter of matching the right prefix, not of tagging entries. */
function invalidateWork() {
  invalidate("/api/work");
}

window.addEventListener("work-updated", invalidateWork);

window.addEventListener("schedule-updated", invalidateSchedule);

/* Invalidate at the transport layer, not at each call site.

   Only ai.js dispatched "schedule-updated"; the Manage sheet, the timetable
   editor, Tasks and Reminders all wrote without announcing it, so a cache
   keyed on that event alone would have shown a deleted lecture until it
   expired. Every write in this app goes through apiFetch, so wrapping it once
   means a future write path cannot forget. */
const _rawApiFetch = window.apiFetch;

window.apiFetch = function patchedApiFetch(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const result = _rawApiFetch(url, options);

  if (method !== "GET" && typeof url === "string" && url.startsWith("/api/")) {
    // Only on success: a rejected write changed nothing, and dropping the
    // cache for it would cost a refetch for no reason.
    return Promise.resolve(result).then((data) => {
      if (url.startsWith("/api/work")) invalidateWork();
    else if (/\/api\/(events|timetable|tasks|reminders)/.test(url)) invalidateSchedule();
      else if (url.startsWith("/api/auth") || url.startsWith("/api/push")) {
        invalidate("/api/auth/me", "/api/onboarding/status", "/api/push/devices");
      }
      return data;
    });
  }
  return result;
};

window.dataStore = {
  cachedFetch, invalidate, invalidateSchedule, invalidateWork, clearCache, stats, cache,
};
