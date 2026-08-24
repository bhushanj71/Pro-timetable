/* Calendar: day/week/month/agenda views backed by /api/events, plus the
   create/edit/delete event modal. */

let calState = { view: "week", anchor: new Date() };

function isoLocal(dt) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
}

function startOfWeek(d) {
  const date = new Date(d);
  const day = date.getDay();
  const diff = day === 0 ? -6 : 1 - day; // Monday start
  date.setDate(date.getDate() + diff);
  date.setHours(0, 0, 0, 0);
  return date;
}

function rangeForView() {
  const a = new Date(calState.anchor);
  if (calState.view === "day") {
    const start = new Date(a); start.setHours(0, 0, 0, 0);
    const end = new Date(a); end.setHours(23, 59, 59, 999);
    return [start, end];
  }
  if (calState.view === "week") {
    const start = startOfWeek(a);
    const end = new Date(start); end.setDate(end.getDate() + 7);
    return [start, end];
  }
  if (calState.view === "month") {
    const start = new Date(a.getFullYear(), a.getMonth(), 1);
    const end = new Date(a.getFullYear(), a.getMonth() + 1, 1);
    return [start, end];
  }
  // agenda: next 14 days
  const start = new Date(a); start.setHours(0, 0, 0, 0);
  const end = new Date(a); end.setDate(end.getDate() + 14);
  return [start, end];
}

function updateLabel(start, end) {
  const opts = { month: "short", day: "numeric", year: "numeric" };
  document.getElementById("cal-label").textContent =
    calState.view === "month"
      ? start.toLocaleDateString([], { month: "long", year: "numeric" })
      : `${start.toLocaleDateString([], opts)} – ${new Date(end - 1).toLocaleDateString([], opts)}`;
}

async function loadEvents(start, end) {
  return apiFetch(`/api/events?start=${start.toISOString()}&end=${end.toISOString()}`);
}

function renderDayLike(container, events, days) {
  let html = `<div class="month-grid" style="grid-template-columns:repeat(${days.length},1fr)">`;
  days.forEach((d) => {
    const isToday = d.toDateString() === new Date().toDateString();
    const dayEvents = events.filter((e) => new Date(e.start_datetime).toDateString() === d.toDateString());
    html += `<div class="month-cell ${isToday ? "today" : ""}">
      <div class="date-num">${d.toLocaleDateString([], { weekday: "short", day: "numeric" })}</div>
      ${dayEvents
        .map(
          (e) => `<div class="tt-event" style="background:${categorySoft(e.event_type)};color:${categoryColor(e.event_type)}" onclick='openEditModal(${JSON.stringify(e).replace(/'/g, "&apos;")})'>${fmtTime(e.start_datetime)} ${esc(e.title)}</div>`
        )
        .join("")}
    </div>`;
  });
  html += `</div>`;
  container.innerHTML = html;
}

function renderMonth(container, events, start) {
  const year = start.getFullYear();
  const month = start.getMonth();
  const firstDay = new Date(year, month, 1);
  const gridStart = startOfWeek(firstDay);
  const days = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(gridStart);
    d.setDate(d.getDate() + i);
    return d;
  });
  let html = `<div class="month-grid">`;
  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach((d) => (html += `<div style="font-weight:700;text-align:center;font-size:.8rem">${d}</div>`));
  days.forEach((d) => {
    const isToday = d.toDateString() === new Date().toDateString();
    const inMonth = d.getMonth() === month;
    const dayEvents = events.filter((e) => new Date(e.start_datetime).toDateString() === d.toDateString());
    html += `<div class="month-cell ${isToday ? "today" : ""}" style="opacity:${inMonth ? 1 : 0.4}">
      <div class="date-num">${d.getDate()}</div>
      ${dayEvents
        .slice(0, 3)
        .map((e) => `<div class="tt-event" style="background:${categorySoft(e.event_type)};color:${categoryColor(e.event_type)}" onclick='openEditModal(${JSON.stringify(e).replace(/'/g, "&apos;")})'>${esc(e.title)}</div>`)
        .join("")}
      ${dayEvents.length > 3 ? `<div class="muted-text">+${dayEvents.length - 3} more</div>` : ""}
    </div>`;
  });
  html += `</div>`;
  container.innerHTML = html;
}

function renderAgenda(container, events) {
  if (!events.length) {
    container.innerHTML = `<p class="muted-text">No events in this period.</p>`;
    return;
  }
  container.innerHTML = `<div class="agenda-list">${events
    .map(
      (e) => `<div class="task-row" onclick='openEditModal(${JSON.stringify(e).replace(/'/g, "&apos;")})' style="cursor:pointer">
      <div class="tl-time">${fmtDate(e.start_datetime)}<br>${fmtTime(e.start_datetime)}</div>
      <div class="tl-dot" style="background:${categoryColor(e.event_type)}"></div>
      <div style="flex:1"><div class="task-name">${esc(e.title)}</div><div class="muted-text">${esc(e.location || "")}</div></div>
      <span class="pill priority-${e.priority}">${e.priority}</span>
    </div>`
    )
    .join("")}</div>`;
}

async function renderCalendar() {
  const [start, end] = rangeForView();
  updateLabel(start, end);
  const container = document.getElementById("cal-view-container");
  try {
    const events = await loadEvents(start, end);
    if (calState.view === "day") renderDayLike(container, events, [start]);
    else if (calState.view === "week") {
      const days = Array.from({ length: 7 }, (_, i) => {
        const d = new Date(start);
        d.setDate(d.getDate() + i);
        return d;
      });
      renderDayLike(container, events, days);
    } else if (calState.view === "month") renderMonth(container, events, start);
    else renderAgenda(container, events);
  } catch (err) {
    container.innerHTML = `<p class="muted-text">Could not load events.</p>`;
  }
}

document.querySelectorAll("[data-view]").forEach((btn) =>
  btn.addEventListener("click", () => {
    calState.view = btn.dataset.view;
    renderCalendar();
  })
);
document.getElementById("cal-prev")?.addEventListener("click", () => {
  const delta = { day: 1, week: 7, month: 30, agenda: 14 }[calState.view];
  calState.anchor.setDate(calState.anchor.getDate() - delta);
  renderCalendar();
});
document.getElementById("cal-next")?.addEventListener("click", () => {
  const delta = { day: 1, week: 7, month: 30, agenda: 14 }[calState.view];
  calState.anchor.setDate(calState.anchor.getDate() + delta);
  renderCalendar();
});
document.getElementById("cal-today")?.addEventListener("click", () => {
  calState.anchor = new Date();
  renderCalendar();
});

/* ---------------- Event modal ---------------- */
function openNewModal() {
  document.getElementById("event-modal-title").textContent = "New Event";
  document.getElementById("ev-id").value = "";
  document.getElementById("ev-title").value = "";
  document.getElementById("ev-type").value = "lecture";
  document.getElementById("ev-priority").value = "medium";
  const now = new Date();
  now.setMinutes(0, 0, 0);
  const end = new Date(now.getTime() + 60 * 60000);
  document.getElementById("ev-start").value = isoLocal(now);
  document.getElementById("ev-end").value = isoLocal(end);
  document.getElementById("ev-location").value = "";
  document.getElementById("ev-description").value = "";
  document.getElementById("ev-delete").classList.add("hidden");
  document.getElementById("event-modal").classList.remove("hidden");
}

function openEditModal(e) {
  document.getElementById("event-modal-title").textContent = "Edit Event";
  document.getElementById("ev-id").value = e.id;
  document.getElementById("ev-title").value = e.title;
  document.getElementById("ev-type").value = e.event_type;
  document.getElementById("ev-priority").value = e.priority;
  document.getElementById("ev-start").value = isoLocal(new Date(e.start_datetime));
  document.getElementById("ev-end").value = isoLocal(new Date(e.end_datetime));
  document.getElementById("ev-location").value = e.location || "";
  document.getElementById("ev-description").value = e.description || "";
  document.getElementById("ev-delete").classList.remove("hidden");
  document.getElementById("event-modal").classList.remove("hidden");
}

document.getElementById("cal-add-event")?.addEventListener("click", openNewModal);
document.getElementById("ev-cancel")?.addEventListener("click", () => document.getElementById("event-modal").classList.add("hidden"));

document.getElementById("ev-save")?.addEventListener("click", async () => {
  const id = document.getElementById("ev-id").value;
  const payload = {
    title: document.getElementById("ev-title").value,
    event_type: document.getElementById("ev-type").value,
    priority: document.getElementById("ev-priority").value,
    start_datetime: new Date(document.getElementById("ev-start").value).toISOString(),
    end_datetime: new Date(document.getElementById("ev-end").value).toISOString(),
    location: document.getElementById("ev-location").value || null,
    description: document.getElementById("ev-description").value || null,
  };
  if (!payload.title) {
    showToast("Title is required", "error");
    return;
  }
  try {
    if (id) {
      await apiFetch(`/api/events/${id}`, { method: "PUT", body: payload });
      showToast("Event updated", "success");
    } else {
      await apiFetch(`/api/events`, { method: "POST", body: payload });
      showToast("Event created", "success");
    }
    document.getElementById("event-modal").classList.add("hidden");
    renderCalendar();
  } catch (err) {
    if (err.status === 409) {
      if (confirm("This conflicts with an existing event. Create anyway?")) {
        await apiFetch(`/api/events?force=true`, { method: "POST", body: payload });
        document.getElementById("event-modal").classList.add("hidden");
        renderCalendar();
      }
    } else {
      showToast(err.message, "error");
    }
  }
});

document.getElementById("ev-delete")?.addEventListener("click", async () => {
  const id = document.getElementById("ev-id").value;
  if (!id || !confirm("Delete this event?")) return;
  try {
    await apiFetch(`/api/events/${id}`, { method: "DELETE" });
    showToast("Event deleted", "success");
    document.getElementById("event-modal").classList.add("hidden");
    renderCalendar();
  } catch (err) {
    showToast(err.message, "error");
  }
});

renderCalendar();
