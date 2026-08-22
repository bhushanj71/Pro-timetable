/* Weekly timetable grid rendering + AI timetable generator UI. */

const TT_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
const TT_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TT_HOURS = Array.from({ length: 11 }, (_, i) => 8 + i); // 8am - 6pm

function timeToRow(iso) {
  const d = new Date(iso);
  const hour = d.getHours() + d.getMinutes() / 60;
  return hour - TT_HOURS[0];
}

async function renderTimetable() {
  const grid = document.getElementById("timetable-grid");
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
    const data = await apiFetch("/api/timetable");
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

function showEventActions(e) {
  const del = confirm(`${e.title}\n${fmtDate(e.start)} ${fmtTime(e.start)}–${fmtTime(e.end)}\n\nDelete this event?`);
  if (del) {
    apiFetch(`/api/events/${e.id}`, { method: "DELETE" })
      .then(() => {
        showToast("Event deleted", "success");
        renderTimetable();
      })
      .catch((err) => showToast(err.message, "error"));
  }
}

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
  try {
    const payload = collectGeneratorPayload();
    const result = await apiFetch("/api/timetable/generate", { method: "POST", body: payload });
    renderGeneratorPreview(result);
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("commit-generator-btn")?.addEventListener("click", async () => {
  try {
    const payload = collectGeneratorPayload();
    const result = await apiFetch("/api/timetable/generate?commit=true", { method: "POST", body: payload });
    showToast(`✓ Timetable saved — ${result.events_created} events created.`, "success");
    document.getElementById("generator-modal").classList.add("hidden");
    renderTimetable();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("export-csv-btn")?.addEventListener("click", () => (window.location.href = "/api/export/csv"));
document.getElementById("export-ics-btn")?.addEventListener("click", () => (window.location.href = "/api/export/ics"));
document.getElementById("export-pdf-btn")?.addEventListener("click", () => (window.location.href = "/api/export/pdf"));

renderTimetable();
