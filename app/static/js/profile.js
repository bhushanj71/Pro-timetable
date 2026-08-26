/* Profile settings form submission. */

document.getElementById("pf-save-btn")?.addEventListener("click", async () => {
  const payload = {
    name: document.getElementById("pf-name").value,
    department: departmentValue() || null,
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


/* ---------------- Department and college options ----------------
   The department is a list with an escape hatch rather than a plain text box,
   because the Work directory groups people by the exact words they typed:
   "Computer" and "Computer Engineering" are two departments as far as a
   search is concerned, and only one of them finds a colleague. */
const OTHER = "__other__";

function departmentValue() {
  const select = document.getElementById("pf-department-select");
  const free = document.getElementById("pf-department");
  if (!select) return free ? free.value.trim() : "";
  return select.value === OTHER ? free.value.trim() : select.value;
}

function showFreeDepartment(show) {
  document.getElementById("pf-department")?.classList.toggle("hidden", !show);
  if (show) document.getElementById("pf-department")?.focus();
}

async function loadProfileOptions() {
  const select = document.getElementById("pf-department-select");
  if (!select) return;

  let data;
  try {
    data = await apiFetch("/api/auth/profile-options");
  } catch {
    // The form still works as free text if this call fails; falling back to
    // an empty dropdown would take the field away entirely.
    select.classList.add("hidden");
    showFreeDepartment(true);
    return;
  }

  const current = (data.current.department || "").trim();
  const known = data.departments.some((d) => d.toLowerCase() === current.toLowerCase());

  select.innerHTML = [
    `<option value="">— Not set —</option>`,
    // An existing value outside the list is kept and preselected, so opening
    // the page and saving cannot silently rewrite what someone already had.
    current && !known ? `<option value="${esc(current)}" selected>${esc(current)}</option>` : "",
    ...data.departments.map((d) =>
      `<option value="${esc(d)}"${d.toLowerCase() === current.toLowerCase() ? " selected" : ""}>${esc(d)}</option>`),
    `<option value="${OTHER}">Other…</option>`,
  ].join("");

  const list = document.getElementById("pf-college-options");
  if (list) list.innerHTML = data.colleges.map((c) => `<option value="${esc(c)}"></option>`).join("");

  const hint = document.getElementById("pf-college-hint");
  if (hint) {
    hint.textContent = data.college_domain
      ? (data.colleges.length
        ? `Suggestions come from colleagues who signed up with an @${data.college_domain} address.`
        : `Nobody with an @${data.college_domain} address has set a college yet — what you type becomes the suggestion for them.`)
      : "Colleagues at your college find each other by this name, so use the same wording they would.";
  }
}

document.getElementById("pf-department-select")?.addEventListener("change", (e) => {
  showFreeDepartment(e.target.value === OTHER);
});

loadProfileOptions();
