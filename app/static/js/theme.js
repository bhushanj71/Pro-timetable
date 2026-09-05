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
  if (meta) meta.setAttribute("content", resolvedTheme() === "dark" ? "#0d0b0a" : "#e0785d");

  updateThemeButton();
  renderAppearance();
}

function updateThemeButton() {
  const btn = document.getElementById("theme-btn");
  if (!btn) return;
  const choice = storedTheme();
  // Show what you'd get, not the abstract setting: "system" displays the
  // icon for whichever theme is actually active.
  const shown = choice === "system" ? resolvedTheme() : choice;
  // The glyph, not the button: writing to btn.textContent would delete the
  // span that the rotation is applied to, and the turn would stop working
  // after the first press.
  const ico = document.getElementById("theme-icon");
  if (ico) ico.textContent = shown === "dark" ? "🌙" : "☀️";
  btn.setAttribute("aria-label", `Theme: ${choice}`);
  btn.title = `Theme: ${choice}`;
}

/* Text size lives with the theme because both are the same kind of choice:
   how the interface should look to this reader on this device. */
const FONT_STEPS = ["small", "medium", "large"];
const FONT_LABEL = { small: "Small", medium: "Medium", large: "Large" };

function storedFont() {
  try {
    const v = localStorage.getItem("profschedule-font");
    return FONT_STEPS.includes(v) ? v : "medium";
  } catch (_) {
    return "medium";
  }
}

function applyFont(size) {
  const value = FONT_STEPS.includes(size) ? size : "medium";
  // Medium is the default, so it carries no attribute at all -- that keeps
  // the common case free of an override to reason about.
  if (value === "medium") document.documentElement.removeAttribute("data-font");
  else document.documentElement.setAttribute("data-font", value);
  try {
    localStorage.setItem("profschedule-font", value);
  } catch (_) {}
  const out = document.getElementById("font-size-value");
  if (out) out.textContent = FONT_LABEL[value];
}

function renderAppearance() {
  const box = document.getElementById("um-appearance");
  if (!box) return;
  const choice = storedTheme();
  const font = storedFont();
  box.innerHTML =
    `<button class="um-item${choice === "system" ? " active" : ""}"
             id="um-system-theme" role="menuitem">
       <span aria-hidden="true">💻</span> Match system
       <span class="um-tick">✓</span>
     </button>` +
    `<div class="font-size-row">
       <div class="font-size-head">
         <span>Text size</span>
         <strong id="font-size-value">${FONT_LABEL[font]}</strong>
       </div>
       <div class="font-size-slider">
         <span class="fs-a">A</span>
         <input type="range" id="font-size-range" min="0" max="2" step="1"
                value="${FONT_STEPS.indexOf(font)}" aria-label="Text size"
                list="font-size-stops">
         <span class="fs-b">A</span>
       </div>
       <datalist id="font-size-stops"><option value="0"></option><option value="1"></option><option value="2"></option></datalist>
     </div>`;
}

document.getElementById("um-appearance")?.addEventListener("input", (e) => {
  if (e.target.id !== "font-size-range") return;
  applyFont(FONT_STEPS[Number(e.target.value)]);
});

document.getElementById("um-appearance")?.addEventListener("click", (e) => {
  if (!e.target.closest("#um-system-theme")) return;
  applyTheme("system", { animate: true });
});

applyFont(storedFont());

/* ---------------- The toggle ----------------

   Skiper26's move: the incoming palette is revealed by a circle growing out
   of the button that was pressed, instead of the page cross-fading into it,
   so the change reads as coming from the control rather than happening to
   the page. The icon turns half a circle with it.

   A press now commits to light or dark. "Follow the system" cannot be one of
   two states, so it moved into the account menu rather than being dropped. */

/* Half a turn per press, and cumulative: resetting to zero between presses
   would leave the glyph upside down every other time. */
let spin = 0;

function turnIcon() {
  const ico = document.getElementById("theme-icon");
  if (!ico) return;
  spin += 180;
  // The standalone property, so it cannot overwrite a transform if this
  // button is ever given one.
  ico.style.rotate = spin + "deg";
}

function revealFrom(el) {
  const box = el.getBoundingClientRect();
  const x = box.left + box.width / 2;
  const y = box.top + box.height / 2;
  // The circle starts at the button and has to reach the furthest corner of
  // the window, or the old palette is left showing in a corner.
  const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  const root = document.documentElement;
  root.style.setProperty("--vt-x", `${x}px`);
  root.style.setProperty("--vt-y", `${y}px`);
  root.style.setProperty("--vt-r", `${r}px`);
}

document.getElementById("theme-btn")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const next = resolvedTheme() === "dark" ? "light" : "dark";
  turnIcon();

  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (still || !document.startViewTransition) {
    // Every browser gets the theme; not every browser gets the circle.
    applyTheme(next, { animate: true });
    return;
  }

  revealFrom(e.currentTarget);
  const root = document.documentElement;
  root.classList.add("vt-theme");
  const vt = document.startViewTransition(() => applyTheme(next));
  // finally, not then: a transition the browser abandons must still take the
  // class off, or every later page navigation animates as a circle.
  vt.finished.finally(() => root.classList.remove("vt-theme"));
});

// Track the OS preference while set to "system".
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (storedTheme() === "system") applyTheme("system");
});

updateThemeButton();
renderAppearance();
