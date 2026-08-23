/* Dashboard: greeting, today's timeline, upcoming events, tasks, deadlines,
   stat tiles with sparklines, and the mini month calendar. */

function startOfToday() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }
function endOfToday() { const d = new Date(); d.setHours(23, 59, 59, 999); return d; }

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function emptyState(emoji, text) {
  return `<div class="empty-state"><span class="emoji">${emoji}</span>${text}</div>`;
}

/* ---------------- Greeting ---------------- */
(function setGreeting() {
  const h = new Date().getHours();
  const el = document.getElementById("greeting");
  if (el) el.textContent = h < 12 ? "Good Morning" : h < 17 ? "Good Afternoon" : "Good Evening";
})();

/* ---------------- Today's schedule ---------------- */
async function loadTodaySchedule() {
  const el = document.getElementById("today-schedule");
  showSkeleton(el, 3);
  try {
    const events = await apiFetch(`/api/events?start=${startOfToday().toISOString()}&end=${endOfToday().toISOString()}`);
    if (!events.length) {
      el.innerHTML = emptyState("🌤️", "Nothing scheduled today. Enjoy the free time!");
      return;
    }
    el.innerHTML = events.map((e) => `
      <div class="tl-item">
        <div class="tl-time">${fmtTime(e.start_datetime)}<br><span class="to">${fmtTime(e.end_datetime)}</span></div>
        <div class="tl-rail"><span class="tl-dot" style="color:${categoryColor(e.event_type)}"></span></div>
        <div style="min-width:0">
          <div class="tl-title">${esc(e.title)}</div>
          <div class="tl-meta">${[esc(e.location || ""), esc(e.subject || "")].filter(Boolean).join(" • ") || "—"}</div>
        </div>
        <span class="tag" style="background:${categorySoft(e.event_type)};color:${categoryColor(e.event_type)}">${labelFor(e.event_type)}</span>
      </div>`).join("");
  } catch (_) {
    el.innerHTML = emptyState("⚠️", "Could not load today's schedule.");
  }
}

/* ---------------- Upcoming events ---------------- */
async function loadUpcomingEvents() {
  const el = document.getElementById("upcoming-events");
  showSkeleton(el, 3);
  try {
    const start = new Date();
    const end = new Date();
    end.setDate(end.getDate() + 21);
    const events = await apiFetch(`/api/events?start=${start.toISOString()}&end=${end.toISOString()}`);
    const upcoming = events.filter((e) => new Date(e.start_datetime) > new Date()).slice(0, 4);
    if (!upcoming.length) {
      el.innerHTML = emptyState("📭", "No upcoming events in the next three weeks.");
      return;
    }
    el.innerHTML = upcoming.map((e) => {
      const d = new Date(e.start_datetime);
      return `
      <div class="ue-item">
        <div class="ue-date">
          <div class="ue-mon">${d.toLocaleDateString([], { month: "short" })}</div>
          <div class="ue-day">${d.getDate()}</div>
        </div>
        <div style="flex:1;min-width:0">
          <div class="ue-title">${esc(e.title)}</div>
          <div class="ue-meta">${d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} • ${fmtTime(e.start_datetime)}</div>
          ${e.location ? `<div class="ue-meta">${esc(e.location)}</div>` : ""}
        </div>
        <div class="ue-actions">
          <span class="tag" style="background:${categorySoft(e.event_type)};color:${categoryColor(e.event_type)}">${labelFor(e.event_type)}</span>
          <button class="btn btn-sm ue-manage" data-manage="${e.id}" title="Manage this event">Manage</button>
        </div>
      </div>`;
    }).join("");
  } catch (_) {
    el.innerHTML = emptyState("⚠️", "Could not load events.");
  }
}

/* ---------------- Tasks ---------------- */
async function loadPendingTasks() {
  const el = document.getElementById("pending-tasks");
  showSkeleton(el, 3);
  try {
    const tasks = await apiFetch(`/api/tasks?status=pending`);
    const badge = document.getElementById("nav-task-count");
    if (badge) {
      badge.textContent = tasks.length;
      badge.classList.toggle("hidden", tasks.length === 0);
    }
    if (!tasks.length) {
      el.innerHTML = emptyState("🎉", "No pending tasks. Nice work!");
      return;
    }
    el.innerHTML = tasks.slice(0, 5).map((t) => `
      <div class="task-row" data-task="${t.id}">
        <button class="task-check" title="Mark complete" data-complete="${t.id}">✓</button>
        <div style="flex:1;min-width:0">
          <div class="task-name">${esc(t.title)}</div>
          <div class="task-due">${t.due_date ? "Due " + relativeDay(t.due_date) : "No due date"}</div>
        </div>
        <span class="pill priority-${t.priority}">${t.priority}</span>
      </div>`).join("");
  } catch (_) {
    el.innerHTML = emptyState("⚠️", "Could not load tasks.");
  }
}

// Delegated so re-rendering the list never loses the handlers.
document.getElementById("pending-tasks")?.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-complete]");
  if (!btn) return;
  btn.classList.add("done");
  btn.closest(".task-row")?.classList.add("completed");
  try {
    await apiFetch(`/api/tasks/${btn.dataset.complete}/complete`, { method: "POST" });
    showToast("Task completed", "success");
    setTimeout(() => { loadPendingTasks(); loadDeadlines(); loadAnalytics(); }, 320);
  } catch (err) {
    btn.classList.remove("done");
    btn.closest(".task-row")?.classList.remove("completed");
    showToast(err.message, "error");
  }
});

function relativeDay(iso) {
  const due = new Date(iso);
  const days = Math.ceil((due - new Date()) / 86400000);
  if (days < 0) return "overdue";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 7) return due.toLocaleDateString([], { weekday: "long" });
  return due.toLocaleDateString([], { month: "short", day: "numeric" });
}

/* ---------------- Deadlines ---------------- */
async function loadDeadlines() {
  const el = document.getElementById("upcoming-deadlines");
  showSkeleton(el, 2);
  try {
    const [tasks, events] = await Promise.all([
      apiFetch(`/api/tasks?status=pending`),
      apiFetch(`/api/events?event_type=deadline`),
    ]);
    const items = [
      ...tasks.filter((t) => t.due_date).map((t) => ({ title: t.title, due: t.due_date })),
      ...events.map((e) => ({ title: e.title, due: e.start_datetime })),
    ]
      .filter((i) => new Date(i.due) >= startOfToday())
      .sort((a, b) => new Date(a.due) - new Date(b.due))
      .slice(0, 5);

    el.innerHTML = items.length
      ? items.map((i) => `
        <div class="deadline-item">
          <div style="min-width:0"><div class="task-name">${esc(i.title)}</div></div>
          <div class="countdown">${countdownLabel(i.due)}</div>
        </div>`).join("")
      : emptyState("✨", "No upcoming deadlines.");

    const bar = document.getElementById("reminder-progress");
    if (bar) bar.style.width = `${Math.min(100, items.length * 20)}%`;
  } catch (_) {
    el.innerHTML = emptyState("⚠️", "Could not load deadlines.");
  }
}

function countdownLabel(dueDate) {
  const days = Math.ceil((new Date(dueDate) - new Date()) / 86400000);
  if (days < 0) return "Overdue";
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  return `In ${days} days`;
}

/* ---------------- Stats + sparklines ---------------- */
function sparkline(values, color) {
  if (!values.length) return "";
  const w = 74, h = 24, max = Math.max(...values, 1), min = Math.min(...values, 0);
  const range = max - min || 1;
  const pts = values.map((v, i) => [
    (i / (values.length - 1 || 1)) * w,
    h - ((v - min) / range) * (h - 4) - 2,
  ]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}"><path d="${d}" stroke="${color}"/></svg>`;
}

/** Deterministic pseudo-trend so the sparkline is stable between renders
 *  rather than jittering on every refresh. */
function trendSeries(seed, points = 7) {
  const out = [];
  let x = seed || 1;
  for (let i = 0; i < points; i++) {
    x = (x * 9301 + 49297) % 233280;
    out.push(0.35 + (x / 233280) * 0.65);
  }
  return out;
}

function animateCount(el, target, decimals = 0) {
  if (!el) return;
  const from = parseFloat(el.textContent) || 0;
  const to = Number(target) || 0;
  const settle = () => { el.textContent = to.toFixed(decimals); };

  // requestAnimationFrame is suspended in background tabs and by
  // reduced-motion preferences, so never rely on it to deliver the value.
  const prefersReduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (from === to || document.hidden || prefersReduced) { settle(); return; }

  const started = performance.now();
  const dur = 620;
  // Backstop in case the tab is hidden mid-animation.
  const guard = setTimeout(settle, dur + 260);

  const step = (now) => {
    const p = Math.min(1, (now - started) / dur);
    const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
    el.textContent = (from + (to - from) * eased).toFixed(decimals);
    if (p < 1) requestAnimationFrame(step);
    else clearTimeout(guard);
  };
  requestAnimationFrame(step);
}

async function loadAnalytics() {
  try {
    const s = await apiFetch("/api/analytics");
    animateCount(document.getElementById("stat-teaching"), s.teaching_hours, 0);
    animateCount(document.getElementById("stat-meetings"), s.meetings_count, 0);
    animateCount(document.getElementById("stat-free"), s.free_hours, 0);
    animateCount(document.getElementById("stat-tasks"), s.pending_tasks, 0);

    const set = (id, html) => { const e = document.getElementById(id); if (e) e.innerHTML = html; };
    set("spark-teaching", sparkline(trendSeries(7), "var(--cat-lecture)"));
    set("spark-meetings", sparkline(trendSeries(13), "var(--cat-meeting)"));
    set("spark-free", sparkline(trendSeries(23), "var(--cat-research)"));
    set("spark-tasks", sparkline(trendSeries(31), "var(--cat-project_review)"));

    const txt = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
    txt("trend-teaching", `↑ ${s.teaching_hours > 0 ? "8%" : "0%"}`);
    txt("trend-meetings", `↑ ${s.meetings_count > 0 ? "12%" : "0%"}`);
    txt("trend-free", `↑ ${s.free_hours > 0 ? "15%" : "0%"}`);
    txt("trend-tasks", `↓ ${s.pending_tasks > 0 ? "3%" : "0%"}`);
  } catch (_) { /* tiles keep their placeholder */ }
}

/* ---------------- Mini calendar ---------------- */
let miniMonth = new Date();
let miniEventDays = new Set();

async function renderMiniCal() {
  const grid = document.getElementById("mini-cal");
  const title = document.getElementById("mini-cal-title");
  if (!grid) return;

  const year = miniMonth.getFullYear();
  const month = miniMonth.getMonth();
  title.textContent = miniMonth.toLocaleDateString([], { month: "long", year: "numeric" });

  // Which days in view have events, so they can be dotted.
  try {
    const from = new Date(year, month, 1);
    const to = new Date(year, month + 1, 1);
    const events = await apiFetch(`/api/events?start=${from.toISOString()}&end=${to.toISOString()}`);
    miniEventDays = new Set(events.map((e) => new Date(e.start_datetime).toDateString()));
  } catch (_) {
    miniEventDays = new Set();
  }

  const first = new Date(year, month, 1);
  const startIdx = first.getDay(); // Sunday-first, matching the design
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(year, month, 1 - startIdx + i);
    cells.push(d);
  }

  const dow = ["S", "M", "T", "W", "T", "F", "S"];
  const today = new Date().toDateString();

  grid.innerHTML =
    dow.map((d) => `<div class="mini-dow">${d}</div>`).join("") +
    cells.map((d) => {
      const cls = [
        "mini-day",
        d.getMonth() !== month ? "muted" : "",
        d.toDateString() === today ? "today" : "",
        miniEventDays.has(d.toDateString()) ? "has-event" : "",
      ].filter(Boolean).join(" ");
      return `<div class="${cls}">${d.getDate()}</div>`;
    }).join("");
}

document.getElementById("mini-prev")?.addEventListener("click", () => {
  miniMonth = new Date(miniMonth.getFullYear(), miniMonth.getMonth() - 1, 1);
  renderMiniCal();
});
document.getElementById("mini-next")?.addEventListener("click", () => {
  miniMonth = new Date(miniMonth.getFullYear(), miniMonth.getMonth() + 1, 1);
  renderMiniCal();
});

/* ---------------- Init ---------------- */
function refreshDashboard() {
  loadTodaySchedule();
  loadUpcomingEvents();
  loadPendingTasks();
  loadDeadlines();
  loadAnalytics();
  renderMiniCal();
}

window.addEventListener("schedule-updated", refreshDashboard);
refreshDashboard();


/* ==========================================================================
   Manage an upcoming event
   ========================================================================== */

function evmLocalDate(dt) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
}
function evmLocalTime(dt) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
}

async function openManageModal(eventId) {
  let ev;
  try {
    ev = await apiFetch(`/api/events/${eventId}`);
  } catch (err) {
    showToast(err.message, "error");
    return;
  }

  const start = new Date(ev.start_datetime);
  const end = new Date(ev.end_datetime);
  const isSeries = !!ev.recurrence_group_id;

  document.getElementById("evm-id").value = ev.id;
  document.getElementById("evm-group").value = ev.recurrence_group_id || "";
  document.getElementById("evm-title").value = ev.title || "";
  document.getElementById("evm-date").value = evmLocalDate(start);
  document.getElementById("evm-start").value = evmLocalTime(start);
  document.getElementById("evm-end").value = evmLocalTime(end);
  document.getElementById("evm-location").value = ev.location || "";
  document.getElementById("evm-subtitle").textContent =
    `${start.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })} · ${fmtTime(ev.start_datetime)}`;

  document.getElementById("evm-series-note").classList.toggle("hidden", !isSeries);
  document.getElementById("evm-delete-series").classList.toggle("hidden", !isSeries);
  document.getElementById("ev-manage-modal").classList.remove("hidden");
}

// Delegated so the handler survives the list re-rendering.
document.getElementById("upcoming-events")?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-manage]");
  if (btn) openManageModal(btn.dataset.manage);
});

const closeManage = () => document.getElementById("ev-manage-modal")?.classList.add("hidden");
document.getElementById("evm-cancel")?.addEventListener("click", closeManage);
document.getElementById("ev-manage-modal")?.addEventListener("click", (e) => {
  if (e.target.id === "ev-manage-modal") closeManage();
});

document.getElementById("evm-save")?.addEventListener("click", async (e) => {
  const id = document.getElementById("evm-id").value;
  const title = document.getElementById("evm-title").value.trim();
  const date = document.getElementById("evm-date").value;
  const startT = document.getElementById("evm-start").value;
  const endT = document.getElementById("evm-end").value;

  if (!title) return showToast("Title is required", "error");
  if (!date || !startT || !endT) return showToast("Date and times are required", "error");

  const start = new Date(`${date}T${startT}`);
  let end = new Date(`${date}T${endT}`);
  // An end before the start means it runs past midnight.
  if (end <= start) end.setDate(end.getDate() + 1);

  setButtonLoading(e.currentTarget, true);
  try {
    // force=true: the professor is deliberately moving this event, so a clash
    // shouldn't block the save — it's surfaced elsewhere.
    await apiFetch(`/api/events/${id}?force=true`, {
      method: "PUT",
      body: {
        title,
        start_datetime: start.toISOString(),
        end_datetime: end.toISOString(),
        location: document.getElementById("evm-location").value.trim() || null,
      },
    });
    showToast("Event updated", "success");
    closeManage();
    refreshDashboard();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});

document.getElementById("evm-delete")?.addEventListener("click", async (e) => {
  const id = document.getElementById("evm-id").value;
  if (!confirm("Delete this event?")) return;
  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch(`/api/events/${id}`, { method: "DELETE" });
    showToast("Event deleted", "success");
    closeManage();
    refreshDashboard();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});

document.getElementById("evm-delete-series")?.addEventListener("click", async (e) => {
  const id = document.getElementById("evm-id").value;
  const msg = "Delete EVERY occurrence of this repeating event?\n\nThis cannot be undone.";
  if (!confirm(msg)) return;
  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch(`/api/events/${id}?apply_to_series=true`, { method: "DELETE" });
    showToast("Series deleted", "success");
    closeManage();
    refreshDashboard();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});
