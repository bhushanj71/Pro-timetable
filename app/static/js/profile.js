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
    out.innerHTML = `Email: <strong>${esc(r.email)}</strong> · Push: <strong>${r.push}</strong> · Devices registered: <strong>${r.devices}</strong>`;
  } catch (err) {
    out.textContent = err.message;
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});



/* ---------------- Install to home screen ---------------- */

function refreshInstallStep() {
  const sub = document.getElementById("pf-install-status");
  const btn = document.getElementById("pf-install-btn");
  const iosHelp = document.getElementById("pf-ios-help");
  if (!sub) return;

  sub.textContent = installStatusText();

  if (isStandalone()) {
    document.getElementById("pf-install-step")?.classList.add("done");
    document.getElementById("pf-install-state")?.classList.remove("hidden");
    btn?.classList.add("hidden");
    iosHelp?.classList.add("hidden");
    return;
  }
  // iOS can't be prompted programmatically, so show the manual steps instead.
  iosHelp?.classList.toggle("hidden", !isIOSDevice());
  btn?.classList.toggle("hidden", !deferredInstallPrompt);
}

document.getElementById("pf-install-btn")?.addEventListener("click", async (e) => {
  setButtonLoading(e.currentTarget, true);
  try {
    const result = await promptInstall();
    if (result === "ios") document.getElementById("pf-ios-help")?.classList.remove("hidden");
  } finally {
    setButtonLoading(e.currentTarget, false);
    refreshInstallStep();
  }
});

document.addEventListener("pwa-installable", refreshInstallStep);
document.addEventListener("pwa-installed", refreshInstallStep);
if (document.getElementById("pf-install-status")) refreshInstallStep();
