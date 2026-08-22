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

document.getElementById("register-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById("register-error");
  errorEl.textContent = "";
  try {
    await apiFetch("/api/auth/register", {
      method: "POST",
      body: {
        name: document.getElementById("name").value,
        email: document.getElementById("email").value,
        password: document.getElementById("password").value,
        timezone: document.getElementById("timezone").value || "Asia/Kolkata",
      },
    });
    window.location.href = "/dashboard";
  } catch (err) {
    errorEl.textContent = err.message || "Registration failed";
  }
});
