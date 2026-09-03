/* Login / registration form handlers. */

document.getElementById("login-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";
  // Raised before the request, not after it: the wait being covered starts at
  // the click, and it also stops a second submit while the first is in flight.
  showRouteVeil("Signing you in…", "Fetching your schedule.");
  try {
    await apiFetch("/api/auth/login", {
      method: "POST",
      body: {
        email: document.getElementById("email").value,
        password: document.getElementById("password").value,
      },
    });
    // Deliberately left up: the dashboard is still loading, and taking the
    // veil down here would flash the empty login form behind it.
    window.location.href = "/dashboard";
  } catch (err) {
    hideRouteVeil();
    errorEl.textContent = err.message || "Login failed";
  }
});

// Reveal the custom start/end inputs only when the preset list can't express
// the professor's timings.
document.getElementById("college-timing")?.addEventListener("change", (e) => {
  const isCustom = e.target.value === "custom";
  document.getElementById("custom-hours-row").classList.toggle("hidden", !isCustom);
  if (!isCustom) {
    const [start, end] = e.target.value.split("-");
    document.getElementById("work-start").value = start;
    document.getElementById("work-end").value = end;
  }
});

function collectWorkingHours() {
  const preset = document.getElementById("college-timing")?.value;
  if (preset && preset !== "custom") {
    const [start, end] = preset.split("-");
    return { start, end };
  }
  return {
    start: document.getElementById("work-start")?.value || "09:00",
    end: document.getElementById("work-end")?.value || "17:00",
  };
}

document.getElementById("register-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById("register-error");
  errorEl.textContent = "";

  const hours = collectWorkingHours();
  if (hours.end <= hours.start) {
    errorEl.textContent = "College end time must be after the start time.";
    return;
  }

  const days = Array.from(document.querySelectorAll(".work-day:checked")).map((cb) => cb.value);
  if (!days.length) {
    errorEl.textContent = "Select at least one working day.";
    return;
  }

  // After every validation gate above, so the veil never covers a form the
  // user still has to correct.
  showRouteVeil("Creating your account…", "Setting up your timetable.");
  try {
    await apiFetch("/api/auth/register", {
      method: "POST",
      body: {
        name: document.getElementById("name").value,
        email: document.getElementById("email").value,
        password: document.getElementById("password").value,
        timezone: document.getElementById("timezone").value || "Asia/Kolkata",
        working_hours_start: hours.start,
        working_hours_end: hours.end,
        lunch_start: document.getElementById("lunch-start").value || "13:00",
        lunch_end: document.getElementById("lunch-end").value || "13:30",
        working_days: days.join(","),
      },
    });
    window.location.href = "/dashboard";
  } catch (err) {
    hideRouteVeil();
    errorEl.textContent = err.message || "Registration failed";
  }
});

/* ---------------- Google Sign-In ---------------- */

// Only show the button when the server actually has OAuth credentials,
// otherwise it would lead to a 503.
(async () => {
  const block = document.getElementById("google-signin-block");
  if (!block) return;
  try {
    const { enabled } = await fetch("/auth/google/status").then((r) => r.json());
    if (enabled) block.classList.remove("hidden");
  } catch (_) {}
})();

// Surface why a Google sign-in bounced back.
(() => {
  const reason = new URLSearchParams(location.search).get("google_error");
  if (!reason) return;
  const messages = {
    cancelled: "Google sign-in was cancelled.",
    missing_code: "Google didn't return an authorisation code. Please try again.",
    exchange_failed: "Could not complete Google sign-in. Please try again.",
    no_email: "Your Google account didn't share an email address.",
    unverified_email: "That Google email isn't verified.",
    deactivated: "This account has been deactivated.",
    account_missing: "Your session expired. Please sign in again.",
  };
  const el = document.getElementById("login-error") || document.getElementById("register-error");
  if (el) el.textContent = messages[reason] || "Google sign-in failed.";
})();


/* Reveal a password field.

   Delegated, because both auth pages have one and neither is worth its own
   listener. Toggling the input's type is what actually works: a CSS-only
   version cannot un-mask a real password field, and swapping in a text input
   loses whatever was already typed.
*/
document.addEventListener("click", (e) => {
  const toggle = e.target.closest(".field-toggle[data-reveal]");
  if (!toggle) return;
  const input = document.getElementById(toggle.dataset.reveal);
  if (!input) return;

  const revealing = input.type === "password";
  input.type = revealing ? "text" : "password";
  toggle.textContent = revealing ? "🙈" : "👁";
  toggle.setAttribute("aria-pressed", String(revealing));
  toggle.setAttribute("aria-label", revealing ? "Hide password" : "Show password");
  // Focus goes back to the field, at the end of what was typed: the point of
  // revealing is to carry on typing, and leaving focus on the button means
  // the next keystroke goes nowhere.
  input.focus();
  const end = input.value.length;
  try { input.setSelectionRange(end, end); } catch (_) { /* not all types allow it */ }
});
