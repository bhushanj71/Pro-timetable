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
  extraction.events.forEach((e) => {
    lines += `<div class="event-line">📌 <strong>${e.title}</strong> — ${e.day || e.date || ""} ${e.start_time}–${e.end_time}${e.recurrence ? ` (repeats ${e.recurrence})` : ""}</div>`;
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

  document.getElementById("ai-confirm-btn")?.addEventListener("click", async () => {
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
    }
  });

  document.getElementById("ai-cancel-btn")?.addEventListener("click", () => {
    box.innerHTML = "";
  });
}

async function submitAIPrompt(promptText) {
  const box = document.getElementById("ai-result");
  box.innerHTML = `<div class="ai-confirm-card">Thinking…</div>`;
  try {
    const response = await apiFetch("/api/ai/process-prompt", { method: "POST", body: { prompt: promptText } });
    lastExtraction = response.extraction;
    renderExtractionPreview(response);
  } catch (err) {
    box.innerHTML = `<div class="ai-confirm-card">${err.message || "AI processing failed"}</div>`;
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
