/* Core client utilities shared across all pages: authenticated fetch,
   toasts, notification bell polling, global search, category colors. */

/* Escape before interpolating anything into innerHTML.

   Event titles, subjects and locations are stored verbatim and can arrive
   from an imported CSV as easily as from the professor's own typing, so a
   title of `<img src=x onerror=...>` would otherwise run script in their
   session with their cookie attached. */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const esc = escapeHtml;

const CATEGORY_COLORS = {
  lecture: "var(--cat-lecture)",
  lab: "var(--cat-lab)",
  meeting: "var(--cat-meeting)",
  project_review: "var(--cat-project_review)",
  examination: "var(--cat-examination)",
  personal: "var(--cat-personal)",
  research: "var(--cat-research)",
  deadline: "var(--cat-deadline)",
  conference: "var(--cat-conference)",
  fdp: "var(--cat-fdp)",
  workshop: "var(--cat-workshop)",
  other: "var(--cat-other)",
};

function categoryColor(type) {
  return CATEGORY_COLORS[type] || CATEGORY_COLORS.other;
}

/** Tinted background matching a category's accent, for tags and badges. */
function categorySoft(type) {
  const key = CATEGORY_COLORS[type] ? type : "other";
  return `var(--cat-${key}-soft)`;
}

const CATEGORY_LABELS = {
  lecture: "Lecture", lab: "Lab", meeting: "Meeting", project_review: "Review",
  examination: "Exam", personal: "Personal", research: "Research",
  deadline: "Deadline", conference: "Conference", fdp: "FDP",
  workshop: "Workshop", other: "Other",
};
function labelFor(type) {
  return CATEGORY_LABELS[type] || "Other";
}

// Set while signing out, so background polling that 401s mid-logout can't
// hijack the redirect and send the user to /login instead of the homepage.
let isSigningOut = false;

async function apiFetch(url, options = {}) {
  const opts = {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);

  const res = await fetch(url, opts);
  if (res.status === 401) {
    // A 401 on a page that needs a session means it expired — send them to
    // sign in again, unless they're deliberately on their way out.
    if (!isSigningOut) window.location.href = "/login";
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    let detail = "Something went wrong";
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  const contentType = res.headers.get("content-type") || "";
  return contentType.includes("application/json") ? res.json() : res.text();
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function fmtDate(iso) {
  return new Date(iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

/* ---------------- Logout ---------------- */
document.getElementById("logout-btn")?.addEventListener("click", async () => {
  isSigningOut = true;
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch (_) {
    // The cookie may already be gone; still send them to the homepage rather
    // than leaving them on a page they can no longer load.
  }
  // replace() so Back doesn't return to the signed-in page they just left.
  window.location.replace("/");
});

/* ---------------- Notification bell ---------------- */

async function pollNotifications() {
  // In Work mode the bell belongs to work-notifications.js. Both writing to
  // the same panel meant whichever ran last won: the badge showed the work
  // count while the list showed personal reminders.
  if (document.body.dataset.profile === "work") return;
  try {
    const data = await apiFetch("/api/reminders/notifications");
    const count = document.getElementById("notif-count");
    const panel = document.getElementById("notif-panel");
    if (!count || !panel) return;

    // The badge tracks *unread*, not merely delivered — otherwise it could
    // never be cleared.
    if (data.unread > 0) {
      count.textContent = data.unread > 9 ? "9+" : data.unread;
      count.classList.remove("hidden");
    } else {
      count.classList.add("hidden");
    }

    const head = `<div class="notif-head">
        <span>Notifications</span>
        <button type="button" class="notif-clear" id="notif-clear"
                ${data.items.length ? "" : "disabled"}>Clear all</button>
      </div>`;

    panel.innerHTML =
      head +
      (data.items.length
        ? data.items
            .map(
              (n) => `<div class="notif-item${n.is_read ? "" : " unread"}">
              🔔 ${esc(n.title || "Reminder")}
              <br><small>${fmtDate(n.reminder_datetime)} · ${fmtTime(n.reminder_datetime)}</small>
            </div>`
            )
            .join("")
        : `<div class="notif-item notif-empty">No notifications yet.</div>`);
  } catch (_) {
    /* silent: polling shouldn't interrupt the page */
  }
}

document.getElementById("notif-bell")?.addEventListener("click", async () => {
  const panel = document.getElementById("notif-panel");
  const opening = panel?.classList.contains("hidden");
  panel?.classList.toggle("hidden");

  // Work mode fills and marks its own panel read.
  if (document.body.dataset.profile === "work") return;
  if (!opening) return;
  // Clear the badge immediately, then persist — the click is the "read".
  document.getElementById("notif-count")?.classList.add("hidden");
  try {
    await apiFetch("/api/reminders/notifications/read", { method: "POST" });
    await pollNotifications();
  } catch (_) {}
});

// Delegated: pollNotifications replaces the panel's contents wholesale, so a
// listener bound to the button itself would be thrown away on every refresh.
document.getElementById("notif-panel")?.addEventListener("click", async (e) => {
  const btn = e.target.closest("#notif-clear");
  if (!btn) return;

  setButtonLoading(btn, true);
  try {
    const { cleared } = await apiFetch("/api/reminders/notifications/clear", { method: "POST" });
    await pollNotifications();
    showToast(cleared ? `Cleared ${cleared} notification${cleared === 1 ? "" : "s"}` : "Nothing to clear", "success");
  } catch (_) {
    showToast("Could not clear notifications", "error");
    setButtonLoading(btn, false);
  }
});

document.addEventListener("click", (e) => {
  const panel = document.getElementById("notif-panel");
  const bell = document.getElementById("notif-bell");
  if (panel && !panel.classList.contains("hidden") && !panel.contains(e.target) && !bell?.contains(e.target)) {
    panel.classList.add("hidden");
  }
});

if (document.getElementById("notif-bell")) {
  pollNotifications();
  setInterval(pollNotifications, 45000);
}

/* ---------------- Skeletons ----------------

   Shown only when there is nothing to show yet. A panel that already holds
   data keeps it while refreshing: replacing real content with grey bars is a
   downgrade, not a loading state. */

const skeleton = {
  // Mirrors .ue-item as it actually renders: badge, title, two meta lines,
  // and the action pills that wrap onto their own row in a narrow card. A
  // skeleton shorter than the content it stands in for just moves the jump
  // rather than removing it.
  rows: (n = 3) =>
    Array.from({ length: n }, () => `
      <div class="sk-row">
        <div class="sk sk-badge"></div>
        <div class="sk-body">
          <div class="sk sk-line medium"></div>
          <div class="sk sk-line short"></div>
          <div class="sk sk-line short"></div>
        </div>
        <div class="sk-row-actions">
          <div class="sk sk-pill"></div>
          <div class="sk sk-pill" style="width:104px"></div>
        </div>
      </div>`).join(""),

  timeline: (n = 3) =>
    Array.from({ length: n }, () => `
      <div class="sk-timeline-row">
        <div class="sk sk-line sk-time"></div>
        <div style="flex:1">
          <div class="sk sk-line medium"></div>
          <div class="sk sk-line short"></div>
        </div>
      </div>`).join(""),

  list: (n = 4) =>
    Array.from({ length: n }, () => `<div class="sk sk-line long" style="height:34px;margin-bottom:10px"></div>`).join(""),

  grid: (n = 24) =>
    `<div class="sk-grid">${Array.from({ length: n }, () => `<div class="sk sk-cell"></div>`).join("")}</div>`,
};

/* ---------------- Loading helpers ---------------- */

// Nothing renders for the first 300ms: most requests finish inside that, and
// flashing a loader for them reads as jank rather than feedback.
const LOADER_DELAY_MS = 300;

/** Markup for the five-dot loader. */
function dotsMarkup(extraClass = "") {
  return `<span class="dots5 ${extraClass}"><span></span><span></span><span></span><span></span><span></span></span>`;
}


/** Toggle a button's spinner. Also disables it, which prevents the
 *  double-submits that previously created duplicate events. */
function setButtonLoading(btn, isLoading, loadingLabel) {
  if (!btn) return;

  if (isLoading) {
    if (!btn.dataset.originalHtml) btn.dataset.originalHtml = btn.innerHTML;
    // Lock the width so the layout doesn't jump when the label is replaced.
    btn.style.minWidth = `${btn.offsetWidth}px`;
    btn.disabled = true;

    // Defer the visual state: a fast action shouldn't flash a loader.
    btn._loadTimer = setTimeout(() => {
      if (loadingLabel) btn.innerHTML = loadingLabel;
      btn.classList.add("is-loading");
      btn.insertAdjacentHTML("beforeend", dotsMarkup("dots5-sm"));
    }, LOADER_DELAY_MS);
  } else {
    clearTimeout(btn._loadTimer);
    btn.classList.remove("is-loading");
    btn.disabled = false;
    btn.querySelector(".dots5")?.remove();
    if (btn.dataset.originalHtml) {
      btn.innerHTML = btn.dataset.originalHtml;
      delete btn.dataset.originalHtml;
    }
    btn.style.minWidth = "";
  }
}

/** Render shimmer placeholder rows into a container while data loads. */
function showSkeleton(container, rows = 3, variant = "rows") {
  if (!container) return;
  // A panel that already holds real content keeps it while refreshing:
  // replacing a rendered schedule with grey bars is a downgrade, not a
  // loading state.
  if (container.dataset.loaded === "1") return;
  const build = skeleton[variant] || skeleton.rows;
  container.innerHTML = build(rows);
}

/** Mark a container as populated, so later refreshes don't flash skeletons. */
function markLoaded(container) {
  if (container) container.dataset.loaded = "1";
}

/** Cover a container with a spinner overlay. Returns a function to remove it. */
function showOverlay(container, label = "Loading…") {
  if (!container) return () => {};
  container.classList.add("loading-host");
  let el = null;
  const timer = setTimeout(() => {
    el = document.createElement("div");
    el.className = "loading-overlay";
    el.innerHTML = `${dotsMarkup()}<span>${label}</span>`;
    container.appendChild(el);
  }, LOADER_DELAY_MS);

  return () => {
    clearTimeout(timer);
    el?.remove();
    container.classList.remove("loading-host");
  };
}

/**
 * "Working on it" panel with rotating status messages.
 */
function startProgress(container, messages, options = {}) {
  if (!container) return { stop() {} };
  const list = messages.length ? messages : ["Working..."];
  let index = 0;
  let msgTimer = null;
  let shown = false;

  const showTimer = setTimeout(() => {
    shown = true;
    container.innerHTML = `
      <div class="ai-result-card">
        <div class="ai-thinking">
          ${dotsMarkup()}
          <span class="ai-thinking-text" id="progress-text">${list[0]}</span>
        </div>
      </div>`;

    const textEl = container.querySelector("#progress-text");
    msgTimer = setInterval(() => {
      index = (index + 1) % list.length;
      if (textEl) textEl.textContent = list[index];
    }, 2200);
  }, options.delay ?? LOADER_DELAY_MS);

  return {
    stop() {
      clearTimeout(showTimer);
      if (msgTimer) clearInterval(msgTimer);
      // Clear the panel only if we actually drew it; otherwise the caller's
      // own result rendering must not be wiped.
      if (shown && options.clearOnStop) container.innerHTML = "";
    },
    get visible() {
      return shown;
    },
  };
}


/* ---------------- Shell interactions ---------------- */

// Mobile sidebar
const _sidebar = document.getElementById("sidebar");
const _scrim = document.getElementById("scrim");
function toggleSidebar(open) {
  if (!_sidebar) return;
  const show = open ?? !_sidebar.classList.contains("open");
  _sidebar.classList.toggle("open", show);
  _scrim?.classList.toggle("hidden", !show);
  document.body.style.overflow = show ? "hidden" : "";
}
document.getElementById("menu-btn")?.addEventListener("click", () => toggleSidebar());
_scrim?.addEventListener("click", () => toggleSidebar(false));
// Navigating on mobile should close the drawer behind you.
_sidebar?.querySelectorAll("a").forEach((a) =>
  a.addEventListener("click", () => window.innerWidth <= 768 && toggleSidebar(false))
);

// Elevate the topbar once the page scrolls beneath it.
const _topbar = document.getElementById("topbar");
if (_topbar) {
  const onScroll = () => _topbar.classList.toggle("is-stuck", window.scrollY > 6);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}

// Escape closes any open overlay.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  document.getElementById("notif-panel")?.classList.add("hidden");
  document.getElementById("theme-menu")?.classList.add("hidden");
  document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((m) => m.classList.add("hidden"));
  if (window.innerWidth <= 768) toggleSidebar(false);
});

// Reflect pending reminders on the sidebar badge too.
async function refreshSidebarBadges() {
  try {
    const rem = await apiFetch("/api/reminders");
    const badge = document.getElementById("nav-reminder-count");
    if (badge) {
      const pending = rem.filter((r) => !r.is_sent).length;
      badge.textContent = pending;
      badge.classList.toggle("hidden", pending === 0);
    }
  } catch (_) {}
}
if (document.getElementById("nav-reminder-count")) refreshSidebarBadges();
