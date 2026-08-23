/* The AI smart-prompt widget: process-prompt -> confirmation -> confirm.
   Reused wherever #ai-prompt-form is present (currently the dashboard). */

let lastExtraction = null;

function renderExtractionPreview(response) {
  const box = document.getElementById("ai-result");
  if (!box) return;

  const { intent, extraction, summary, conflicts, requires_confirmation } = response;

  if (intent === "FIND_FREE_TIME" || intent === "QUERY_SCHEDULE") {
    box.innerHTML = `<div class="ai-confirm-card"><div>${summary}</div></div>`;
    return;
  }

  let lines = "";
  // The model often omits end_time; the server derives a default duration, so
  // mirror that here rather than rendering a literal "null".
  const impliedEnd = (start, end) => {
    if (end) return end;
    if (!start || !/^\d{1,2}:\d{2}$/.test(start)) return "";
    const [h, m] = start.split(":").map(Number);
    return `${String((h + 1) % 24).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  };

  extraction.events.forEach((e) => {
    const when = [e.day, e.date].filter(Boolean).join(" ");
    const end = impliedEnd(e.start_time, e.end_time);
    const range = [e.start_time, end].filter(Boolean).join("–");
    const days = e.recurrence_days?.length ? e.recurrence_days.join(", ") : null;
    const repeat = e.recurrence ? ` (every ${days || e.recurrence.replace("weekly", "week")})` : "";
    lines += `<div class="event-line">📌 <strong>${e.title}</strong> — ${days || when} ${range}${repeat}</div>`;
  });
  extraction.reminders.forEach((r) => {
    lines += `<div class="event-line">⏰ Reminder: <strong>${r.title}</strong> ${r.date || ""} ${r.time || ""}</div>`;
  });
  extraction.tasks.forEach((t) => {
    lines += `<div class="event-line">✅ Task: <strong>${t.title}</strong> ${t.due_date ? "due " + t.due_date : ""}</div>`;
  });

  let conflictHtml = "";
  if (conflicts && conflicts.length) {
    conflictHtml = `<div class="event-line" style="color:#ffb4a2">⚠️ Conflicts with: ${conflicts
      .map((c) => c.conflicts_with.map((cw) => cw.title).join(", "))
      .join("; ")}</div>`;
  }

  box.innerHTML = `
    <div class="ai-confirm-card">
      <div><strong>I understood the following:</strong></div>
      ${lines || "<div class='event-line'>Nothing actionable found.</div>"}
      ${conflictHtml}
      ${extraction.notes ? `<div class="event-line" style="opacity:.8">${extraction.notes}</div>` : ""}
      ${
        requires_confirmation && lines
          ? `<div class="ai-confirm-actions">
              <button class="btn btn-primary btn-sm" id="ai-confirm-btn">Confirm Schedule</button>
              <button class="btn btn-sm" id="ai-cancel-btn">Cancel</button>
            </div>`
          : ""
      }
    </div>`;

  document.getElementById("ai-confirm-btn")?.addEventListener("click", async (ev) => {
    const confirmBtn = ev.currentTarget;
    const cancelBtn = document.getElementById("ai-cancel-btn");
    setButtonLoading(confirmBtn, true);
    if (cancelBtn) cancelBtn.disabled = true;
    try {
      const result = await apiFetch("/api/ai/confirm", { method: "POST", body: { extraction: lastExtraction } });
      showToast(
        `✓ Schedule created — ${result.events_created} event(s), ${result.reminders_created} reminder(s), ${result.tasks_created} task(s).`,
        "success"
      );
      box.innerHTML = "";
      document.getElementById("ai-prompt-input").value = "";
      window.dispatchEvent(new CustomEvent("schedule-updated"));
    } catch (err) {
      showToast(err.message || "Could not save schedule", "error");
      setButtonLoading(confirmBtn, false);
      if (cancelBtn) cancelBtn.disabled = false;
    }
  });

  document.getElementById("ai-cancel-btn")?.addEventListener("click", () => {
    box.innerHTML = "";
  });
}

const AI_PROGRESS_MESSAGES = [
  "Reading your request…",
  "Working out dates and times…",
  "Extracting the schedule details…",
  "Checking for clashes with your timetable…",
  "Almost there…",
];

async function submitAIPrompt(promptText) {
  const box = document.getElementById("ai-result");
  const submitBtn = document.querySelector("#ai-prompt-form button[type=submit]");
  const input = document.getElementById("ai-prompt-input");

  const progress = startProgress(box, AI_PROGRESS_MESSAGES);
  setButtonLoading(submitBtn, true);
  if (input) input.disabled = true;

  try {
    const response = await apiFetch("/api/ai/process-prompt", { method: "POST", body: { prompt: promptText } });
    lastExtraction = response.extraction;
    progress.stop();
    renderExtractionPreview(response);
  } catch (err) {
    progress.stop();
    box.innerHTML = `<div class="ai-confirm-card">⚠️ ${err.message || "AI processing failed"}</div>`;
  } finally {
    progress.stop();
    setButtonLoading(submitBtn, false);
    if (input) input.disabled = false;
  }
}

document.getElementById("ai-prompt-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("ai-prompt-input");
  if (input.value.trim()) submitAIPrompt(input.value.trim());
});

document.querySelectorAll(".ai-example-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const input = document.getElementById("ai-prompt-input");
    input.value = chip.dataset.prompt;
    submitAIPrompt(chip.dataset.prompt);
  });
});
