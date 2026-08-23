/* Core client utilities shared across all pages: authenticated fetch,
   toasts, notification bell polling, global search, category colors. */

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
  try {
    const notifs = await apiFetch("/api/reminders/notifications");
    const count = document.getElementById("notif-count");
    const panel = document.getElementById("notif-panel");
    if (!count || !panel) return;

    if (notifs.length > 0) {
      count.textContent = notifs.length;
      count.classList.remove("hidden");
    } else {
      count.classList.add("hidden");
    }

    panel.innerHTML = notifs.length
      ? notifs.map((n) => `<div class="notif-item">🔔 ${n.title || "Reminder"}<br><small>${fmtDate(n.reminder_datetime)} · ${fmtTime(n.reminder_datetime)}</small></div>`).join("")
      : `<div class="notif-item">No notifications yet.</div>`;
  } catch (_) {
    /* silent: notification polling shouldn't interrupt the page */
  }
}

document.getElementById("notif-bell")?.addEventListener("click", () => {
  document.getElementById("notif-panel")?.classList.toggle("hidden");
});

document.addEventListener("click", (e) => {
  const panel = document.getElementById("notif-panel");
  const bell = document.getElementById("notif-bell");
  if (panel && !panel.classList.contains("hidden") && !panel.contains(e.target) && e.target !== bell) {
    panel.classList.add("hidden");
  }
});

if (document.getElementById("notif-bell")) {
  pollNotifications();
  setInterval(pollNotifications, 45000);
}

/* ---------------- Global search ---------------- */
let searchDebounce;
document.getElementById("global-search")?.addEventListener("input", (e) => {
  clearTimeout(searchDebounce);
  const q = e.target.value.trim();
  const results = document.getElementById("search-results");
  if (!q) {
    results.classList.add("hidden");
    return;
  }
  searchDebounce = setTimeout(async () => {
    try {
      const data = await apiFetch(`/api/search?q=${encodeURIComponent(q)}`);
      const items = [
        ...data.events.map((e) => `<div class="result-item">📅 ${e.title} <small>(${fmtDate(e.start)})</small></div>`),
        ...data.tasks.map((t) => `<div class="result-item">✅ ${t.title}</div>`),
      ];
      results.innerHTML = items.length ? items.join("") : `<div class="result-item">No results</div>`;
      results.classList.remove("hidden");
    } catch (_) {
      results.classList.add("hidden");
    }
  }, 250);
});

document.addEventListener("click", (e) => {
  const results = document.getElementById("search-results");
  const input = document.getElementById("global-search");
  if (results && !results.contains(e.target) && e.target !== input) {
    results.classList.add("hidden");
  }
});

/* ---------------- Loading helpers ---------------- */

/** Toggle a button's spinner. Also disables it, which prevents the
 *  double-submits that previously created duplicate events. */
function setButtonLoading(btn, isLoading, loadingLabel) {
  if (!btn) return;
  if (isLoading) {
    if (!btn.dataset.originalHtml) btn.dataset.originalHtml = btn.innerHTML;
    // Lock the current width so the layout doesn't jump when the label hides.
    btn.style.minWidth = `${btn.offsetWidth}px`;
    if (loadingLabel) btn.innerHTML = loadingLabel;
    btn.classList.add("is-loading");
    btn.disabled = true;
  } else {
    btn.classList.remove("is-loading");
    btn.disabled = false;
    if (btn.dataset.originalHtml) {
      btn.innerHTML = btn.dataset.originalHtml;
      delete btn.dataset.originalHtml;
    }
    btn.style.minWidth = "";
  }
}

/** Render shimmer placeholder rows into a container while data loads. */
function showSkeleton(container, rows = 3) {
  if (!container) return;
  container.innerHTML = Array.from({ length: rows })
    .map(() => `<div class="skeleton wide"></div><div class="skeleton half"></div>`)
    .join("");
}

/** Cover a container with a spinner overlay. Returns a function to remove it. */
function showOverlay(container, label = "Loading…") {
  if (!container) return () => {};
  container.classList.add("loading-host");
  const el = document.createElement("div");
  el.className = "loading-overlay";
  el.innerHTML = `<span class="spinner"></span><span>${label}</span>`;
  container.appendChild(el);
  return () => {
    el.remove();
    container.classList.remove("loading-host");
  };
}

/**
 * Animated "working on it" panel with a live elapsed timer and rotating
 * status messages. AI calls routinely run several seconds, and a static
 * label makes that feel like the app has hung.
 */
function startProgress(container, messages, { showElapsed = true } = {}) {
  if (!container) return { stop() {} };
  const list = messages.length ? messages : ["Working…"];
  let index = 0;
  const startedAt = Date.now();

  container.innerHTML = `
    <div class="ai-confirm-card">
      <div class="ai-thinking">
        <span class="spinner spinner-lg"></span>
        <span class="ai-thinking-text" id="progress-text">${list[0]}</span>
        ${showElapsed ? '<span class="ai-thinking-elapsed" id="progress-elapsed">0.0s</span>' : ""}
      </div>
    </div>`;

  const textEl = container.querySelector("#progress-text");
  const elapsedEl = container.querySelector("#progress-elapsed");

  const msgTimer = setInterval(() => {
    index = (index + 1) % list.length;
    if (textEl) textEl.textContent = list[index];
  }, 2200);

  const tickTimer = elapsedEl
    ? setInterval(() => {
        elapsedEl.textContent = `${((Date.now() - startedAt) / 1000).toFixed(1)}s`;
      }, 100)
    : null;

  return {
    stop() {
      clearInterval(msgTimer);
      if (tickTimer) clearInterval(tickTimer);
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

// Keyboard: Cmd/Ctrl+K focuses search, Escape closes overlays.
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    document.getElementById("global-search")?.focus();
  }
  if (e.key === "Escape") {
    document.getElementById("search-results")?.classList.add("hidden");
    document.getElementById("notif-panel")?.classList.add("hidden");
    document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((m) => m.classList.add("hidden"));
    if (window.innerWidth <= 768) toggleSidebar(false);
  }
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
