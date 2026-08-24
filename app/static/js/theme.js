/* Theme switcher: light, dark, or follow the system.

   The initial theme is applied by a small inline script in <head> so the
   correct palette is painted on the first frame. This file only handles the
   switcher UI and subsequent changes. */

const THEME_KEY = "profschedule-theme";
const THEMES = [
  { id: "light", label: "Light", icon: "☀️" },
  { id: "dark", label: "Dark", icon: "🌙" },
  { id: "system", label: "System", icon: "💻" },
];

function storedTheme() {
  try {
    return localStorage.getItem(THEME_KEY) || "system";
  } catch (_) {
    return "system"; // private mode with storage disabled
  }
}

function resolvedTheme() {
  const choice = storedTheme();
  if (choice !== "system") return choice;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(choice, { animate = false } = {}) {
  const root = document.documentElement;

  if (animate) {
    root.classList.add("theme-animating");
    setTimeout(() => root.classList.remove("theme-animating"), 320);
  }

  if (choice === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", choice);
  }

  try {
    localStorage.setItem(THEME_KEY, choice);
  } catch (_) {}

  // Keep the browser/OS chrome in step with the page.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", resolvedTheme() === "dark" ? "#171311" : "#e0785d");

  updateThemeButton();
  renderThemeMenu();
}

function updateThemeButton() {
  const btn = document.getElementById("theme-btn");
  if (!btn) return;
  const choice = storedTheme();
  // Show what you'd get, not the abstract setting: "system" displays the
  // icon for whichever theme is actually active.
  const shown = choice === "system" ? resolvedTheme() : choice;
  btn.textContent = shown === "dark" ? "🌙" : "☀️";
  btn.setAttribute("aria-label", `Theme: ${choice}`);
  btn.title = `Theme: ${choice}`;
}

function renderThemeMenu() {
  const menu = document.getElementById("theme-menu");
  if (!menu) return;
  const choice = storedTheme();
  menu.innerHTML = THEMES.map(
    (t) => `<button class="theme-option ${t.id === choice ? "active" : ""}" data-theme-choice="${t.id}">
              <span>${t.icon}</span> ${esc(t.label)} <span class="tick">✓</span>
            </button>`
  ).join("");
}

document.getElementById("theme-btn")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = document.getElementById("theme-menu");
  if (!menu) return;
  renderThemeMenu();
  menu.classList.toggle("hidden");
});

document.getElementById("theme-menu")?.addEventListener("click", (e) => {
  const opt = e.target.closest("[data-theme-choice]");
  if (!opt) return;
  applyTheme(opt.dataset.themeChoice, { animate: true });
  document.getElementById("theme-menu").classList.add("hidden");
});

document.addEventListener("click", (e) => {
  const menu = document.getElementById("theme-menu");
  const btn = document.getElementById("theme-btn");
  if (menu && !menu.classList.contains("hidden") && !menu.contains(e.target) && !btn?.contains(e.target)) {
    menu.classList.add("hidden");
  }
});

// Track the OS preference while set to "system".
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (storedTheme() === "system") applyTheme("system");
});

updateThemeButton();
renderThemeMenu();
