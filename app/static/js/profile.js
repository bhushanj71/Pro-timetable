/* Profile settings form submission. */

/* An emptied number input reads "", and parseInt("") is NaN -- which becomes
   null over JSON and means "leave this alone" to the server. The fallback is
   what makes clearing a field mean what it looks like it means. */
function num(id, fallback) {
  const raw = document.getElementById(id)?.value;
  const parsed = parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

document.getElementById("pf-save-btn")?.addEventListener("click", async () => {
  const payload = {
    name: document.getElementById("pf-name").value,
    // College and department are not here: they are ids now, validated as a
    // pair, and saved through /api/org/profile just below.
    designation: document.getElementById("pf-designation").value || null,
    working_days: document.getElementById("pf-working-days").value,
    timezone: document.getElementById("pf-timezone").value,
    working_hours_start: document.getElementById("pf-hours-start").value,
    working_hours_end: document.getElementById("pf-hours-end").value,
    lunch_start: document.getElementById("pf-lunch-start").value,
    lunch_end: document.getElementById("pf-lunch-end").value,
    default_lecture_duration: parseInt(document.getElementById("pf-lecture-duration").value, 10),
    default_reminder_minutes: parseInt(document.getElementById("pf-reminder-minutes").value, 10),
    preferred_ai_provider: document.getElementById("pf-ai-provider").value,

    // Planning constraints. num() rather than parseInt directly, because an
    // emptied number field yields "" -> NaN, and NaN serialises to null, which
    // the server would read as "leave unchanged" instead of "they cleared it".
    day_start: document.getElementById("pf-day-start").value,
    day_end: document.getElementById("pf-day-end").value,
    dinner_start: document.getElementById("pf-dinner-start").value,
    dinner_end: document.getElementById("pf-dinner-end").value,
    exercise_minutes: num("pf-exercise-minutes", 0),
    exercise_when: document.getElementById("pf-exercise-when").value,
    commute_minutes: num("pf-commute", 0),
    focus_period: document.getElementById("pf-focus").value,
    study_target_minutes: num("pf-study-target", 0),
    break_minutes: num("pf-break", 0),
    study_block_min: num("pf-block-min", 45),
    study_block_max: num("pf-block-max", 120),
    subject_priorities: document.getElementById("pf-subjects").value.trim() || null,
  };
  try {
    await apiFetch("/api/auth/me", { method: "PUT", body: payload });
    await saveOrgFields();
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


/* ---------------- College and department ----------------
   Both are relational now: the form posts ids, and the server checks that the
   department actually belongs to the college before storing either. The
   free-text columns are kept in step server-side so the rest of the app --
   exports, the admin list -- keeps reading what it always did. */
async function loadOrgFields() {
  const collegeSel = document.getElementById("pf-college-select");
  const deptSel = document.getElementById("pf-department-select");
  if (!collegeSel) return;

  try {
    const p = await apiFetch("/api/org/profile");
    collegeSel.innerHTML = `<option value="">— Not set —</option>` + p.colleges.map((c) =>
      `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
    if (p.college?.id) collegeSel.value = p.college.id;
    else if (p.colleges.length === 1) collegeSel.value = p.colleges[0].id;

    await fillProfileDepartments(collegeSel.value, p.department?.id);
  } catch {
    // A failed lookup must not leave someone staring at "Loading…" with no
    // way to save the rest of the form.
    collegeSel.innerHTML = `<option value="">Could not load colleges</option>`;
    deptSel.innerHTML = `<option value="">Could not load departments</option>`;
  }
}

async function fillProfileDepartments(collegeId, selected) {
  const deptSel = document.getElementById("pf-department-select");
  if (!deptSel) return;
  if (!collegeId) {
    deptSel.innerHTML = `<option value="">Select your department…</option>`;
    return;
  }
  const { departments } = await apiFetch(`/api/org/colleges/${collegeId}/departments`);
  deptSel.innerHTML = `<option value="">Select your department…</option>` +
    departments.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
  if (selected) deptSel.value = selected;
}

document.getElementById("pf-college-select")?.addEventListener("change", (e) =>
  fillProfileDepartments(e.target.value));

/* Saved separately from the rest of the profile: this pair is validated as a
   pair, and half of it is meaningless. */
async function saveOrgFields() {
  const college_id = document.getElementById("pf-college-select")?.value;
  const department_id = document.getElementById("pf-department-select")?.value;
  if (!college_id || !department_id) return;
  await apiFetch("/api/org/profile", { method: "PUT", body: { college_id, department_id } });
}

loadOrgFields();
