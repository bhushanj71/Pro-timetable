/* The AI smart-prompt widget: process-prompt -> confirmation -> confirm.
   Reused wherever #ai-prompt-form is present (currently the dashboard). */

let lastExtraction = null;

function renderExtractionPreview(response) {
  const box = document.getElementById("ai-result");
  if (!box) return;

  const { intent, extraction, summary, conflicts, requires_confirmation } = response;

  if (intent === "FIND_FREE_TIME" || intent === "QUERY_SCHEDULE") {
    box.innerHTML = `<div class="ai-confirm-card"><div>${esc(summary)}</div></div>`;
    return;
  }

  /* Look-ups: the server has already answered, so there is nothing to
     confirm. Rendering these through the create path is what produced
     "Nothing actionable found" for a perfectly good question. */
  if (intent === "OUT_OF_SCOPE") {
    box.innerHTML = `<div class="ai-result-card">💬 ${esc(summary)}</div>`;
    return;
  }

  if (["GET_NEXT_CLASS", "SHOW_LOCATION", "CHECK_CONFLICTS", "VIEW_REMINDERS"].includes(intent)) {
    const rows = (response.matches || []).map((m) => {
      // VIEW_REMINDERS returns reminders; the others return events.
      if (m.minutes_before !== undefined) {
        return `<div class="event-line">🔔 <strong>${esc(m.event_title || m.title)}</strong> — ${esc(m.local)}${
          m.minutes_before != null ? ` · ${m.minutes_before} min before` : ""}</div>`;
      }
      const where = m.location ? ` · 📍 ${esc(m.location)}` : "";
      const who = m.faculty ? ` · ${esc(m.faculty)}` : "";
      const map = m.map_url
        ? ` <a class="loc-btn" href="${esc(m.map_url)}" target="_blank" rel="noopener noreferrer">Open in Maps</a>`
        : "";
      return `<div class="event-line">📚 <strong>${esc(m.title)}</strong> — ${esc(m.when)}${who}${where}${map}</div>`;
    }).join("");

    const clashes = (response.conflicts || []).map(
      (c) => `<div class="event-line">⚠️ <strong>${esc(c.a.title)}</strong> clashes with <strong>${esc(c.b.title)}</strong> — ${esc(c.a.when)}${
        c.kind !== "time" ? ` (same ${esc(c.kind)})` : ""}</div>`
    ).join("");

    box.innerHTML = `
      <div class="ai-result-card">
        <div><strong>${esc(summary)}</strong></div>
        ${rows}${clashes}
      </div>`;
    return;
  }

  if (intent === "CANCEL_DAY" && requires_confirmation) {
    const rows = (response.matches || []).map(
      (m) => `<div class="event-line">\u{1F6AB} <strong>${esc(m.title)}</strong> \u2014 ${esc(m.when)}${
        m.location ? " \u00b7 " + esc(m.location) : ""}</div>`
    ).join("");
    box.innerHTML = `
      <div class="ai-result-card">
        <div><strong>${esc(summary)}</strong></div>
        ${rows}
        <div class="event-line" style="opacity:.75">These stay in your timetable as cancelled, so you can put the day back if it changes.</div>
        <div class="modal-actions">
          <button class="btn btn-danger" id="ai-confirm-btn">Cancel the day</button>
          <button class="btn" id="ai-cancel-btn">Keep them</button>
        </div>
      </div>`;
    wireConfirmButtons(box, "cancel_day");
    return;
  }

  /* Changing a reminder rule is a write, so it is confirmed -- but it must be
     described as the rule change it is. Falling through to the create-reminder
     preview announced it as a brand new reminder. */
  if (intent === "UPDATE_REMINDER" || intent === "DELETE_REMINDER") {
    const scope = extraction.reminder_scope || extraction.target_event_title || "your upcoming classes";
    const mins = extraction.reminder_minutes_before;
    const off = intent === "DELETE_REMINDER";
    box.innerHTML = `
      <div class="ai-result-card">
        <div><strong>I understood the following:</strong></div>
        <div class="event-line">🔔 ${off
          ? `Turn <strong>off</strong> reminders for <strong>${esc(scope)}</strong>`
          : `Remind <strong>${mins != null ? mins + " minutes" : "the usual time"}</strong> before <strong>${esc(scope)}</strong>`}</div>
        <div class="modal-actions">
          <button class="btn ${off ? "btn-danger" : "btn-primary"}" id="ai-confirm-btn">${off ? "Turn off" : "Apply"}</button>
          <button class="btn" id="ai-cancel-btn">Cancel</button>
        </div>
      </div>`;
    wireConfirmButtons(box, off ? "delete" : "update");
    return;
  }

  /* Reminder rules and other server-executed actions come back already done.
     A tick is a claim that something happened, so it is only used when
     something did: "I couldn't find that event" is not a success. */
  if (requires_confirmation === false && summary) {
    const failed = /couldn't|could not|no match|nothing|not find/i.test(summary);
    box.innerHTML = `<div class="ai-result-card">${failed ? "🔍" : "✓"} ${esc(summary)}</div>`;
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
      .map((m) => `<div class="event-line">${verb} <strong>${esc(m.title)}</strong> — ${esc(m.when)}${m.location ? " · " + esc(m.location) : ""}</div>`)
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

  // --- Create: list what will be added ---
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
    const range = [e.start_time, impliedEnd(e.start_time, e.end_time)].filter(Boolean).join("–");
    const days = e.recurrence_days?.length ? e.recurrence_days.join(", ") : null;
    const repeat = e.recurrence ? ` (every ${days || e.recurrence.replace("weekly", "week")})` : "";
    lines += `<div class="event-line">📌 <strong>${esc(e.title)}</strong> — ${days || when} ${range}${repeat}</div>`;
  });

  extraction.reminders.forEach((r) => {
    lines += `<div class="event-line">⏰ Reminder: <strong>${esc(r.title)}</strong> ${r.date || ""} ${r.time || ""}</div>`;
  });

  extraction.tasks.forEach((t) => {
    lines += `<div class="event-line">✅ Task: <strong>${esc(t.title)}</strong> ${t.due_date ? "due " + t.due_date : ""}</div>`;
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
      if (action === "cancel_day") {
        showToast(result.message || `✓ Cancelled ${result.cancelled} class(es).`, "success");
      } else if (action === "delete") {
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

/* Read the result aloud.

   A professor who spoke the command is, by definition, not looking at the
   screen -- their hands are full or they are walking to class. Silent success
   is indistinguishable from nothing having happened. */
function speak(text) {
  try {
    if (!("speechSynthesis" in window) || !text) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(String(text).slice(0, 240));
    u.lang = navigator.language || "en-IN";
    u.rate = 1.02;
    window.speechSynthesis.speak(u);
  } catch (_) {
    /* Speech output is a nicety; never let it break the action. */
  }
}

/* Which spoken commands run without a second tap.

   Creating and updating are recoverable -- the event is right there to edit
   or remove. Deleting is not, and a misheard word is exactly how you lose a
   lecture you meant to keep, so deletions always stop to ask. */
function autoRunnable(response) {
  if (response.requires_confirmation === false) return false;   // already done
  // Cancelling a day clears every class on it. "Tomorrow is a holiday" is an
  // easy thing to mishear out of ordinary conversation near an open mic, and
  // the blast radius is a whole day, so it is shown before it happens.
  const alwaysAsk = ["delete", "cancel_day"];
  return !alwaysAsk.includes(response.action) && response.intent !== "DELETE_EVENT";
}

async function submitAIPrompt(promptText, { spoken = false } = {}) {
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

    if (spoken) {
      if (autoRunnable(response)) {
        // Hands-free means the command completes, not that it queues up a
        // button for the mouse the professor is not holding.
        await runConfirmedAction(response);
      } else {
        speak(response.summary || "");
      }
    }
  } catch (err) {
    progress.stop();
    box.innerHTML = `<div class="ai-confirm-card">⚠️ ${esc(err.message || "AI processing failed")}</div>`;
  } finally {
    progress.stop();
    setButtonLoading(submitBtn, false);
    if (input) input.disabled = false;
  }
}

/** Execute the pending extraction and say what happened. */
async function runConfirmedAction(response) {
  const box = document.getElementById("ai-result");
  try {
    const result = await apiFetch("/api/ai/confirm", { method: "POST", body: { extraction: lastExtraction } });

    if (result.ok === false) {
      const msg = result.message || "I couldn't find that in your schedule.";
      box.innerHTML = `<div class="ai-result-card">🔍 ${esc(msg)}</div>`;
      showToast(msg, "error");
      speak(msg);
      return;
    }

    const said = result.message || summariseResult(result, response);
    box.innerHTML = `<div class="ai-result-card">✓ ${esc(said)}</div>`;
    showToast(said, "success");
    speak(said);
    // Same signal the confirm button sends, so the timetable, counters and
    // upcoming list all repaint through the one existing path.
    document.getElementById("ai-prompt-input").value = "";
    window.dispatchEvent(new CustomEvent("schedule-updated"));
  } catch (err) {
    const msg = err.message || "That didn't go through.";
    box.innerHTML = `<div class="ai-result-card">⚠️ ${esc(msg)}</div>`;
    speak(msg);
  }
}

/** A sentence for results that don't carry their own message. */
function summariseResult(result, response) {
  const bits = [];
  if (result.events_created) bits.push(`${result.events_created} event${result.events_created > 1 ? "s" : ""} added`);
  if (result.reminders_created) bits.push(`${result.reminders_created} reminder${result.reminders_created > 1 ? "s" : ""} set`);
  if (result.tasks_created) bits.push(`${result.tasks_created} task${result.tasks_created > 1 ? "s" : ""} added`);
  if (result.updated) bits.push(`${result.updated} updated`);
  if (result.deleted) bits.push(`${result.deleted} deleted`);
  return bits.length ? bits.join(", ") : (response.summary || "Done");
}

document.getElementById("ai-prompt-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("ai-prompt-input");
  // voice.js sets this immediately before submitting, and it is cleared here
  // so a later typed command is never mistaken for a spoken one.
  const spoken = window.__profscheduleSpokenSubmit === true;
  window.__profscheduleSpokenSubmit = false;
  if (input.value.trim()) submitAIPrompt(input.value.trim(), { spoken });
});

document.querySelectorAll(".ai-example-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const input = document.getElementById("ai-prompt-input");
    input.value = chip.dataset.prompt;
    submitAIPrompt(chip.dataset.prompt);
  });
});
