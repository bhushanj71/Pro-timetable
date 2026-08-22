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
