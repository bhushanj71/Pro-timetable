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

async function apiFetch(url, options = {}) {
  const opts = {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);

  const res = await fetch(url, opts);
  if (res.status === 401) {
    window.location.href = "/login";
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
  await apiFetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
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
