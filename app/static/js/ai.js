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

  // Update and delete act on events that already exist, so show those rather
  // than the "will be created" list.
  if (response.action === "delete" || response.action === "update") {
    if (!response.matches.length) {
      box.innerHTML = `<div class="ai-result-card">🔍 ${summary}</div>`;
      return;
    }
    const verb = response.action === "delete" ? "🗑️ Delete" : "✏️ Update";
    lines = response.matches
      .map((m) => `<div class="event-line">${verb} <strong>${m.title}</strong> — ${m.when}${m.location ? " · " + m.location : ""}</div>`)
      .join("");

    const ex = extraction;
    if (response.action === "update") {
      const bits = [];
      if (ex.new_day || ex.new_date) bits.push(`move to <strong>${ex.new_day || ex.new_date}</strong>`);
      if (ex.new_start_time) bits.push(`start <strong>${ex.new_start_time}</strong>`);
      if (ex.new_end_time) bits.push(`end <strong>${ex.new_end_time}</strong>`);
      if (bits.length) lines += `<div class="event-line">→ ${bits.join(", ")}</div>`;
    }
    if (ex.apply_to_series) {
      lines += `<div class="event-line">⚠️ Applies to the entire repeating series</div>`;
    }

    box.innerHTML = `
      <div class="ai-result-card">
        <div><strong>I understood the following:</strong></div>
        ${lines}
        ${ex.notes ? `<div class="event-line" style="opacity:.75">${ex.notes}</div>` : ""}
        <div class="modal-actions">
          <button class="btn ${response.action === "delete" ? "btn-danger" : "btn-primary"}" id="ai-confirm-btn">
            ${response.action === "delete" ? "Yes, delete" : "Apply change"}
          </button>
          <button class="btn" id="ai-cancel-btn">Cancel</button>
        </div>
      </div>`;
    wireConfirmButtons(box, response.action);
    return;
  }

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

  wireConfirmButtons(box, "create");
}

function wireConfirmButtons(box, action) {
  document.getElementById("ai-confirm-btn")?.addEventListener("click", async (ev) => {
    const confirmBtn = ev.currentTarget;
    const cancelBtn = document.getElementById("ai-cancel-btn");
    setButtonLoading(confirmBtn, true);
    if (cancelBtn) cancelBtn.disabled = true;
    try {
      const result = await apiFetch("/api/ai/confirm", { method: "POST", body: { extraction: lastExtraction } });
      if (result.ok === false) {
        showToast(result.message || "Nothing matched", "error");
        setButtonLoading(confirmBtn, false);
        if (cancelBtn) cancelBtn.disabled = false;
        return;
      }
      if (action === "delete") {
        showToast(`✓ Deleted ${result.deleted} event(s).`, "success");
      } else if (action === "update") {
        showToast(`✓ Updated ${result.updated} event(s).`, "success");
      } else {
        showToast(
          `✓ Schedule created — ${result.events_created} event(s), ${result.reminders_created} reminder(s), ${result.tasks_created} task(s).`,
          "success"
        );
      }
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
