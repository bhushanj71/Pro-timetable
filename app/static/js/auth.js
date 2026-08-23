/* Login / registration form handlers. */

document.getElementById("login-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";
  try {
    await apiFetch("/api/auth/login", {
      method: "POST",
      body: {
        email: document.getElementById("email").value,
        password: document.getElementById("password").value,
      },
    });
    window.location.href = "/dashboard";
  } catch (err) {
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
    errorEl.textContent = err.message || "Registration failed";
  }
});
