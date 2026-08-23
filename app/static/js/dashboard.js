/* Dashboard: today's schedule, upcoming events, pending tasks, deadlines, analytics tiles. */

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}
function endOfToday() {
  const d = new Date();
  d.setHours(23, 59, 59, 999);
  return d;
}

async function loadTodaySchedule() {
  const el = document.getElementById("today-schedule");
  showSkeleton(el, 3);
  try {
    const events = await apiFetch(`/api/events?start=${startOfToday().toISOString()}&end=${endOfToday().toISOString()}`);
    el.innerHTML = events.length
      ? events
          .map(
            (e) => `
        <div class="schedule-item">
          <div class="schedule-time">${fmtTime(e.start_datetime)}</div>
          <div class="category-dot" style="background:${categoryColor(e.event_type)}"></div>
          <div style="flex:1">
            <div class="schedule-title">${e.title}</div>
            <div class="schedule-sub">${e.location || ""}</div>
          </div>
          <span class="badge-pill priority-${e.priority}">${e.priority}</span>
        </div>`
          )
          .join("")
      : `<p class="schedule-sub">Nothing scheduled today. Enjoy the free time!</p>`;
  } catch (_) {
    el.innerHTML = `<p class="schedule-sub">Could not load schedule.</p>`;
  }
}

async function loadUpcomingEvents() {
  const el = document.getElementById("upcoming-events");
  showSkeleton(el, 3);
  try {
    const start = new Date();
    const end = new Date();
    end.setDate(end.getDate() + 14);
    const events = await apiFetch(`/api/events?start=${start.toISOString()}&end=${end.toISOString()}`);
    const upcoming = events.filter((e) => new Date(e.start_datetime) > new Date()).slice(0, 8);
    el.innerHTML = upcoming.length
      ? upcoming
          .map(
            (e) => `
        <div class="schedule-item">
          <div class="category-dot" style="background:${categoryColor(e.event_type)}"></div>
          <div style="flex:1">
            <div class="schedule-title">${e.title}</div>
            <div class="schedule-sub">${fmtDate(e.start_datetime)} · ${fmtTime(e.start_datetime)}</div>
          </div>
        </div>`
          )
          .join("")
      : `<p class="schedule-sub">No upcoming events in the next two weeks.</p>`;
  } catch (_) {
    el.innerHTML = `<p class="schedule-sub">Could not load events.</p>`;
  }
}

async function loadPendingTasks() {
  const el = document.getElementById("pending-tasks");
  showSkeleton(el, 3);
  try {
    const tasks = await apiFetch(`/api/tasks?status=pending`);
    el.innerHTML = tasks.length
      ? tasks
          .slice(0, 8)
          .map(
            (t) => `
        <div class="schedule-item">
          <div style="flex:1">
            <div class="schedule-title">${t.title}</div>
            ${t.due_date ? `<div class="schedule-sub">Due ${fmtDate(t.due_date)}</div>` : ""}
          </div>
          <button class="btn btn-sm" onclick="completeTaskFromDashboard('${t.id}')">✓ Done</button>
        </div>`
          )
          .join("")
      : `<p class="schedule-sub">No pending tasks. Nice work!</p>`;
  } catch (_) {
    el.innerHTML = `<p class="schedule-sub">Could not load tasks.</p>`;
  }
}

async function completeTaskFromDashboard(taskId) {
  try {
    await apiFetch(`/api/tasks/${taskId}/complete`, { method: "POST" });
    showToast("Task marked complete", "success");
    loadPendingTasks();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function countdownLabel(dueDate) {
  const now = new Date();
  const due = new Date(dueDate);
  const diffMs = due - now;
  const diffDays = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
  if (diffDays < 0) return "Overdue";
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Tomorrow";
  return `Due in ${diffDays} days`;
}

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
      .slice(0, 6);

    el.innerHTML = items.length
      ? items
          .map(
            (i) => `
        <div class="deadline-item">
          <div>${i.title}</div>
          <div class="countdown">${countdownLabel(i.due)}</div>
        </div>`
          )
          .join("")
      : `<p class="schedule-sub">No upcoming deadlines.</p>`;
  } catch (_) {
    el.innerHTML = `<p class="schedule-sub">Could not load deadlines.</p>`;
  }
}

async function loadAnalytics() {
  try {
    const stats = await apiFetch("/api/analytics");
    document.getElementById("stat-teaching").textContent = stats.teaching_hours;
    document.getElementById("stat-meetings").textContent = stats.meetings_count;
    document.getElementById("stat-free").textContent = stats.free_hours;
    document.getElementById("stat-tasks").textContent = stats.pending_tasks;
  } catch (_) {}
}

function refreshDashboard() {
  loadTodaySchedule();
  loadUpcomingEvents();
  loadPendingTasks();
  loadDeadlines();
  loadAnalytics();
}

window.addEventListener("schedule-updated", refreshDashboard);
refreshDashboard();
