/* Weekly timetable grid rendering + AI timetable generator UI. */

const TT_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
const TT_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TT_HOURS = Array.from({ length: 11 }, (_, i) => 8 + i); // 8am - 6pm

function timeToRow(iso) {
  const d = new Date(iso);
  const hour = d.getHours() + d.getMinutes() / 60;
  return hour - TT_HOURS[0];
}

// Which week the grid is showing: 0 = current, 1 = next, -1 = previous.
let ttWeekOffset = 0;

async function renderTimetable() {
  const grid = document.getElementById("timetable-grid");
  // Clear any "no classes" note from a previous render so it can't stack up.
  grid.parentElement.querySelectorAll(".tt-empty-note").forEach((n) => n.remove());
  grid.style.gridTemplateRows = `40px repeat(${TT_HOURS.length}, 60px)`;

  let html = `<div class="tt-header">Time</div>`;
  TT_DAY_LABELS.forEach((d) => (html += `<div class="tt-header">${d}</div>`));

  TT_HOURS.forEach((h) => {
    const label = h % 12 === 0 ? 12 : h % 12;
    const ampm = h < 12 ? "AM" : "PM";
    html += `<div class="tt-time">${label} ${ampm}</div>`;
    TT_DAYS.forEach((day) => (html += `<div class="tt-cell" data-day="${day}" data-hour="${h}"></div>`));
  });

  grid.innerHTML = html;

  try {
    const data = await apiFetch(`/api/timetable?week_offset=${ttWeekOffset}`);

    const label = document.getElementById("tt-week-label");
    if (label && data.week_start) {
      const start = new Date(data.week_start + "T00:00:00");
      const end = new Date(start);
      end.setDate(end.getDate() + 6);
      const opts = { month: "short", day: "numeric" };
      const rel = ttWeekOffset === 0 ? " (this week)" : ttWeekOffset === 1 ? " (next week)" : "";
      label.textContent = `${start.toLocaleDateString([], opts)} – ${end.toLocaleDateString([], { ...opts, year: "numeric" })}${rel}`;
    }

    if (!data.events.length) {
      const note = document.createElement("p");
      note.className = "schedule-sub tt-empty-note";
      note.style.marginTop = "10px";
      note.textContent = "No classes scheduled this week. Use Prev/Next to check other weeks.";
      grid.parentElement.appendChild(note);
    }

    data.events.forEach((e) => {
      const start = new Date(e.start);
      const end = new Date(e.end);
      const hour = start.getHours();
      if (hour < TT_HOURS[0] || hour > TT_HOURS[TT_HOURS.length - 1]) return;
      const cell = grid.querySelector(`.tt-cell[data-day="${e.day}"][data-hour="${hour}"]`);
      if (!cell) return;
      const evEl = document.createElement("div");
      evEl.className = "tt-event";
      evEl.style.background = categoryColor(e.event_type);
      evEl.title = e.title;
      evEl.innerHTML = `${e.title}<div class="tt-event-time">${fmtTime(e.start)}–${fmtTime(e.end)}</div>`;
      evEl.addEventListener("click", () => showEventActions(e));
      cell.appendChild(evEl);
    });
  } catch (err) {
    showToast("Could not load timetable", "error");
  }
}

function ttIsoLocal(dt) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
}

function ttSetDays(codes) {
  document.querySelectorAll(".tt-day").forEach((cb) => {
    cb.checked = (codes || []).includes(cb.value);
  });
}

function ttGetDays() {
  return Array.from(document.querySelectorAll(".tt-day:checked")).map((cb) => cb.value);
}

/** Open the editor for an existing class. Full details are fetched because the
 *  grid payload is a trimmed projection. */
async function showEventActions(e) {
  let full;
  try {
    full = await apiFetch(`/api/events/${e.id}`);
  } catch (err) {
    showToast(err.message, "error");
    return;
  }

  document.getElementById("tt-modal-title").textContent = "Edit Class";
  document.getElementById("tt-ev-id").value = full.id;
  document.getElementById("tt-ev-group").value = full.recurrence_group_id || "";
  document.getElementById("tt-ev-title").value = full.title || "";
  document.getElementById("tt-ev-subject").value = full.subject || "";
  document.getElementById("tt-ev-type").value = full.event_type || "lecture";
  document.getElementById("tt-ev-start").value = ttIsoLocal(new Date(full.start_datetime));
  document.getElementById("tt-ev-end").value = ttIsoLocal(new Date(full.end_datetime));
  document.getElementById("tt-ev-location").value = full.location || "";
  document.getElementById("tt-ev-priority").value = full.priority || "medium";

  // Recurrence is fixed once a series exists; changing it would mean
  // rebuilding every occurrence, so editing applies to this event (or the
  // whole series via the dedicated button) instead.
  document.getElementById("tt-repeat-group").classList.add("hidden");
  ttSetDays([]);

  document.getElementById("tt-ev-delete").classList.remove("hidden");
  document.getElementById("tt-ev-delete-series").classList.toggle("hidden", !full.recurrence_group_id);
  document.getElementById("tt-event-modal").classList.remove("hidden");
}

function openAddClassModal() {
  document.getElementById("tt-modal-title").textContent = "Add Class";
  document.getElementById("tt-ev-id").value = "";
  document.getElementById("tt-ev-group").value = "";
  document.getElementById("tt-ev-title").value = "";
  document.getElementById("tt-ev-subject").value = "";
  document.getElementById("tt-ev-type").value = "lecture";
  document.getElementById("tt-ev-location").value = "";
  document.getElementById("tt-ev-priority").value = "medium";

  // Default to the next hour on the Monday of the week being viewed.
  const base = new Date();
  base.setDate(base.getDate() - ((base.getDay() + 6) % 7) + ttWeekOffset * 7);
  base.setHours(9, 0, 0, 0);
  const end = new Date(base.getTime() + 60 * 60000);
  document.getElementById("tt-ev-start").value = ttIsoLocal(base);
  document.getElementById("tt-ev-end").value = ttIsoLocal(end);

  document.getElementById("tt-repeat-group").classList.remove("hidden");
  ttSetDays([]);
  document.getElementById("tt-ev-delete").classList.add("hidden");
  document.getElementById("tt-ev-delete-series").classList.add("hidden");
  document.getElementById("tt-event-modal").classList.remove("hidden");
}

document.getElementById("tt-add-btn")?.addEventListener("click", openAddClassModal);
document.getElementById("tt-ev-cancel")?.addEventListener("click", () =>
  document.getElementById("tt-event-modal").classList.add("hidden")
);

document.getElementById("tt-ev-save")?.addEventListener("click", async () => {
  const id = document.getElementById("tt-ev-id").value;
  const title = document.getElementById("tt-ev-title").value.trim();
  const startVal = document.getElementById("tt-ev-start").value;
  const endVal = document.getElementById("tt-ev-end").value;

  if (!title) return showToast("Title is required", "error");
  if (!startVal || !endVal) return showToast("Start and end time are required", "error");
  if (new Date(endVal) <= new Date(startVal)) return showToast("End time must be after the start time", "error");

  const payload = {
    title,
    subject: document.getElementById("tt-ev-subject").value.trim() || null,
    event_type: document.getElementById("tt-ev-type").value,
    start_datetime: new Date(startVal).toISOString(),
    end_datetime: new Date(endVal).toISOString(),
    location: document.getElementById("tt-ev-location").value.trim() || null,
    priority: document.getElementById("tt-ev-priority").value,
  };

  try {
    if (id) {
      await apiFetch(`/api/events/${id}?force=true`, { method: "PUT", body: payload });
      showToast("Class updated", "success");
    } else {
      const days = ttGetDays();
      if (days.length) payload.recurrence_rule = `weekly:${days.join(",")}`;
      await apiFetch("/api/events?force=true", { method: "POST", body: payload });
      showToast(days.length ? "Recurring class added" : "Class added", "success");
    }
    document.getElementById("tt-event-modal").classList.add("hidden");
    renderTimetable();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("tt-ev-delete")?.addEventListener("click", async () => {
  const id = document.getElementById("tt-ev-id").value;
  if (!id || !confirm("Delete just this one class?")) return;
  try {
    await apiFetch(`/api/events/${id}`, { method: "DELETE" });
    showToast("Class deleted", "success");
    document.getElementById("tt-event-modal").classList.add("hidden");
    renderTimetable();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("tt-ev-delete-series")?.addEventListener("click", async () => {
  const id = document.getElementById("tt-ev-id").value;
  if (!id || !confirm("Delete EVERY occurrence of this recurring class?\n\nThis cannot be undone.")) return;
  try {
    await apiFetch(`/api/events/${id}?apply_to_series=true`, { method: "DELETE" });
    showToast("Series deleted", "success");
    document.getElementById("tt-event-modal").classList.add("hidden");
    renderTimetable();
  } catch (err) {
    showToast(err.message, "error");
  }
});

/* ---------------- Reset / bulk delete ---------------- */
const TT_RESET_LABELS = {
  week: "the week currently shown",
  future: "ALL upcoming events",
  past: "all past events",
  all: "your ENTIRE schedule",
};

function ttUpdateResetWarning() {
  const scope = document.getElementById("tt-reset-scope").value;
  const subject = document.getElementById("tt-reset-subject").value.trim();
  document.getElementById("tt-reset-warning").textContent =
    `This will delete ${TT_RESET_LABELS[scope]}${subject ? ` for subject matching "${subject}"` : ""}.`;
}

document.getElementById("tt-reset-btn")?.addEventListener("click", () => {
  document.getElementById("tt-reset-subject").value = "";
  ttUpdateResetWarning();
  document.getElementById("tt-reset-modal").classList.remove("hidden");
});
document.getElementById("tt-reset-cancel")?.addEventListener("click", () =>
  document.getElementById("tt-reset-modal").classList.add("hidden")
);
document.getElementById("tt-reset-scope")?.addEventListener("change", ttUpdateResetWarning);
document.getElementById("tt-reset-subject")?.addEventListener("input", ttUpdateResetWarning);

document.getElementById("tt-reset-confirm")?.addEventListener("click", async () => {
  const scope = document.getElementById("tt-reset-scope").value;
  const subject = document.getElementById("tt-reset-subject").value.trim();

  // Deleting everything is unrecoverable, so require typing the word.
  if (scope === "all" && !subject) {
    const typed = prompt('This deletes your ENTIRE schedule and cannot be undone.\n\nType DELETE to confirm:');
    if (typed !== "DELETE") return showToast("Cancelled — nothing was deleted", "error");
  }

  try {
    const qs = new URLSearchParams({ confirm: "true", scope, week_offset: String(ttWeekOffset) });
    if (subject) qs.set("subject", subject);
    const res = await apiFetch(`/api/events?${qs}`, { method: "DELETE" });
    showToast(`Deleted ${res.deleted} event(s)`, "success");
    document.getElementById("tt-reset-modal").classList.add("hidden");
    renderTimetable();
  } catch (err) {
    showToast(err.message, "error");
  }
});

/* ---------------- Generator ---------------- */
document.getElementById("open-generator-btn")?.addEventListener("click", () => {
  document.getElementById("generator-modal").classList.toggle("hidden");
});

function addSubjectRow(values = {}) {
  const container = document.getElementById("subject-rows");
  const row = document.createElement("div");
  row.className = "form-row subject-row";
  row.innerHTML = `
    <div class="form-group"><label>Subject</label><input type="text" class="subj-name" value="${values.subject || ""}" placeholder="Artificial Neural Network"></div>
    <div class="form-group" style="max-width:140px"><label>Lectures/wk</label><input type="number" class="subj-count" value="${values.count || 3}" min="1" max="10"></div>
    <div class="form-group" style="max-width:140px"><label>Duration (min)</label><input type="number" class="subj-duration" value="${values.duration || 60}" min="30" step="30"></div>
    <button class="btn btn-sm btn-danger" onclick="this.closest('.subject-row').remove()" style="align-self:flex-end;margin-bottom:14px">✕</button>
  `;
  container.appendChild(row);
}
document.getElementById("add-subject-row")?.addEventListener("click", () => addSubjectRow());
if (document.getElementById("subject-rows")) addSubjectRow();

function collectGeneratorPayload() {
  const subjects = Array.from(document.querySelectorAll(".subject-row"))
    .map((row) => ({
      subject: row.querySelector(".subj-name").value.trim(),
      lectures_per_week: parseInt(row.querySelector(".subj-count").value, 10),
      duration_minutes: parseInt(row.querySelector(".subj-duration").value, 10),
    }))
    .filter((s) => s.subject);

  const [hoursStart, hoursEnd] = document.getElementById("gen-hours").value.split("-");
  const [lunchStart, lunchEnd] = document.getElementById("gen-lunch").value.split("-");

  return {
    subjects,
    working_days: document.getElementById("gen-days").value.split(",").map((d) => d.trim()),
    working_hours_start: hoursStart?.trim() || "09:00",
    working_hours_end: hoursEnd?.trim() || "17:00",
    lunch_start: lunchStart?.trim() || null,
    lunch_end: lunchEnd?.trim() || null,
    avoid_after: document.getElementById("gen-avoid-after").value.trim() || null,
  };
}

function renderGeneratorPreview(result) {
  const el = document.getElementById("generator-result");
  const days = [...new Set(Object.keys(result.grid))];
  if (!days.length) {
    el.innerHTML = `<p class="form-error">Nothing could be scheduled. Check that you entered a subject name and that your working hours leave room for the lectures.</p>`;
    return;
  }
  let html = `<table style="width:100%;border-collapse:collapse;font-size:0.82rem">`;
  days.forEach((day) => {
    const slots = Object.entries(result.grid[day]).filter(([, subj]) => subj);
    html += `<tr><td style="padding:4px;font-weight:700">${day}</td><td style="padding:4px">${
      slots.map(([time, subj]) => `${time} ${subj}`).join(", ") || "—"
    }</td></tr>`;
  });
  html += `</table>`;
  if (result.unscheduled?.length) {
    html += `<p class="form-error">Could not fully place: ${result.unscheduled
      .map((u) => `${u.subject} (${u.placed}/${u.requested})`)
      .join(", ")}</p>`;
  }
  el.innerHTML = html;
}

document.getElementById("run-generator-btn")?.addEventListener("click", async () => {
  const payload = collectGeneratorPayload();
  if (!payload.subjects.length) {
    showToast("Enter at least one subject name before generating (the example text in the box is just a placeholder).", "error");
    return;
  }
  try {
    const result = await apiFetch("/api/timetable/generate", { method: "POST", body: payload });
    renderGeneratorPreview(result);
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("commit-generator-btn")?.addEventListener("click", async () => {
  const payload = collectGeneratorPayload();
  if (!payload.subjects.length) {
    showToast("Enter at least one subject name before generating (the example text in the box is just a placeholder).", "error");
    return;
  }
  try {
    const result = await apiFetch("/api/timetable/generate?commit=true", { method: "POST", body: payload });
    showToast(`✓ Timetable saved — ${result.events_created} events created.`, "success");
    document.getElementById("generator-modal").classList.add("hidden");
    renderTimetable();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("tt-prev")?.addEventListener("click", () => {
  ttWeekOffset -= 1;
  renderTimetable();
});
document.getElementById("tt-next")?.addEventListener("click", () => {
  ttWeekOffset += 1;
  renderTimetable();
});
document.getElementById("tt-today")?.addEventListener("click", () => {
  ttWeekOffset = 0;
  renderTimetable();
});

document.getElementById("export-csv-btn")?.addEventListener("click", () => (window.location.href = "/api/export/csv"));
document.getElementById("export-ics-btn")?.addEventListener("click", () => (window.location.href = "/api/export/ics"));
document.getElementById("export-pdf-btn")?.addEventListener("click", () => (window.location.href = "/api/export/pdf"));

renderTimetable();
