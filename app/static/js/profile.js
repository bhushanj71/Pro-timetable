/* Profile settings form submission. */

document.getElementById("pf-save-btn")?.addEventListener("click", async () => {
  const payload = {
    name: document.getElementById("pf-name").value,
    department: document.getElementById("pf-department").value || null,
    designation: document.getElementById("pf-designation").value || null,
    college: document.getElementById("pf-college").value || null,
    working_days: document.getElementById("pf-working-days").value,
    timezone: document.getElementById("pf-timezone").value,
    working_hours_start: document.getElementById("pf-hours-start").value,
    working_hours_end: document.getElementById("pf-hours-end").value,
    lunch_start: document.getElementById("pf-lunch-start").value,
    lunch_end: document.getElementById("pf-lunch-end").value,
    default_lecture_duration: parseInt(document.getElementById("pf-lecture-duration").value, 10),
    default_reminder_minutes: parseInt(document.getElementById("pf-reminder-minutes").value, 10),
    preferred_ai_provider: document.getElementById("pf-ai-provider").value,
  };
  try {
    await apiFetch("/api/auth/me", { method: "PUT", body: payload });
    showToast("Profile updated", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
});

/* ---------------- Notification preferences ---------------- */

async function loadCalendarUrl() {
  const input = document.getElementById("pf-calendar-url");
  if (!input) return;
  try {
    const { url } = await apiFetch("/api/calendar-feed-url");
    input.value = url;
  } catch (_) {
    input.value = "Could not load link";
  }
}

document.getElementById("pf-copy-url")?.addEventListener("click", async () => {
  const input = document.getElementById("pf-calendar-url");
  try {
    await navigator.clipboard.writeText(input.value);
    showToast("Calendar link copied", "success");
  } catch (_) {
    // Clipboard API needs a secure context; fall back to selecting the text.
    input.select();
    showToast("Press Ctrl/Cmd+C to copy", "info");
  }
});

document.getElementById("pf-rotate-url")?.addEventListener("click", async (e) => {
  e.preventDefault();
  if (!confirm("Reset the calendar link?\n\nAny calendar app already subscribed will stop updating and must be re-added.")) return;
  try {
    await apiFetch("/api/calendar-feed-url/rotate", { method: "POST" });
    await loadCalendarUrl();
    showToast("Calendar link reset", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("pf-save-notify")?.addEventListener("click", async (e) => {
  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch("/api/auth/me", {
      method: "PUT",
      body: {
        notify_email: document.getElementById("pf-notify-email").checked,
        notify_push: document.getElementById("pf-notify-push").checked,
      },
    });
    showToast("Notification preferences saved", "success");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});

document.getElementById("pf-test-notify")?.addEventListener("click", async (e) => {
  const out = document.getElementById("pf-test-result");
  setButtonLoading(e.currentTarget, true);
  out.textContent = "";
  try {
    const r = await apiFetch("/api/notifications/test", { method: "POST" });
    out.innerHTML = `Email: <strong>${r.email}</strong> · Push: <strong>${r.push}</strong> · Devices registered: <strong>${r.devices}</strong>`;
  } catch (err) {
    out.textContent = err.message;
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});

loadCalendarUrl();
