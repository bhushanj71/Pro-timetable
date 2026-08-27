/* Phone-portrait behaviour: page transitions, the "More" tab, and the
   single-day timetable that replaces the 7-column grid. */

const isPhone = () => window.matchMedia("(max-width: 768px)").matches;

/* ---------------- Page transitions ---------------- */

// Fade the outgoing page instead of letting it blank out. Skipped when the
// browser handles cross-document view transitions itself.
(() => {
  const nativeVT = "startViewTransition" in document;
  if (nativeVT || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  document.addEventListener("click", (e) => {
    const link = e.target.closest("a[href]");
    if (!link) return;

    const url = new URL(link.href, location.href);
    const plain = !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0;
    const sameSite = url.origin === location.origin;
    const isHash = url.pathname === location.pathname && url.hash;

    if (!plain || !sameSite || isHash || link.target === "_blank" || link.hasAttribute("download")) return;

    e.preventDefault();
    document.body.classList.add("is-leaving");
    // Match the CSS pageOut duration, with a fallback so a slow paint can't
    // strand the user on a faded page.
    setTimeout(() => (location.href = url.href), 165);
  });

  // Restore on back/forward, which reuses the faded DOM from bfcache.
  window.addEventListener("pageshow", () => document.body.classList.remove("is-leaving"));
})();

/* ---------------- "More" tab opens the drawer ---------------- */
document.getElementById("bn-more")?.addEventListener("click", () => {
  document.getElementById("sidebar")?.classList.add("open");
  document.getElementById("scrim")?.classList.remove("hidden");
  document.body.style.overflow = "hidden";
});

/* ---------------- Mirror the task badge onto the tab bar ---------------- */
(() => {
  const source = document.getElementById("nav-task-count");
  const target = document.getElementById("bn-task-count");
  if (!source || !target) return;
  const sync = () => {
    target.textContent = source.textContent;
    target.classList.toggle("hidden", source.classList.contains("hidden"));
  };
  new MutationObserver(sync).observe(source, {
    childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["class"],
  });
  sync();
})();

/* ---------------- Single-day timetable ---------------- */

const MOBILE_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
const MOBILE_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
let mobileSelectedDay = null;

/** Called by timetable.js after it loads a week. */
function renderMobileTimetable(data) {
  const host = document.getElementById("tt-dayview");
  const bar = document.getElementById("tt-daybar");
  if (!host || !bar || !isPhone()) return;

  const weekStart = new Date(data.week_start + "T00:00:00");
  const byDay = {};
  data.events.forEach((e) => (byDay[e.day] = byDay[e.day] || []).push(e));

  // Default to today when viewing the current week, otherwise Monday.
  if (mobileSelectedDay === null) {
    const todayCode = MOBILE_DAYS[(new Date().getDay() + 6) % 7];
    mobileSelectedDay = byDay[todayCode] ? todayCode : "MON";
  }

  bar.innerHTML = MOBILE_DAYS.map((code, i) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    const isToday = d.toDateString() === new Date().toDateString();
    return `<button class="tt-day-btn ${code === mobileSelectedDay ? "active" : ""} ${byDay[code] ? "has-events" : ""}"
              data-day="${code}" ${isToday ? 'aria-current="date"' : ""}>
              <span>${MOBILE_DAY_LABELS[i]}</span>
              <span class="tt-day-num">${d.getDate()}</span>
            </button>`;
  }).join("");

  bar.querySelectorAll(".tt-day-btn").forEach((b) =>
    b.addEventListener("click", () => {
      mobileSelectedDay = b.dataset.day;
      renderMobileTimetable(data);
    })
  );

  const events = (byDay[mobileSelectedDay] || []).sort((a, b) => new Date(a.start) - new Date(b.start));
  host.innerHTML = events.length
    ? events
        .map(
          (e) => `
      <div class="tl-item" data-event-id="${e.id}">
        <div class="tl-time">${fmtTime(e.start)}<br><span class="to">${fmtTime(e.end)}</span></div>
        <div class="tl-rail"><span class="tl-dot" style="color:${categoryColor(e.event_type)}"></span></div>
        <div style="min-width:0">
          <div class="tl-title">${esc(e.title)}</div>
          <div class="tl-meta">${[e.location, e.subject].filter(Boolean).join(" • ") || "—"}</div>
        </div>
        <span class="tag" style="background:${categorySoft(e.event_type)};color:${categoryColor(e.event_type)}">${labelFor(e.event_type)}</span>
      </div>`
        )
        .join("")
    : `<div class="empty-state"><span class="emoji">🌤️</span>Nothing scheduled this day.</div>`;

  // Tapping a class opens the same editor the desktop grid uses.
  host.querySelectorAll(".tl-item").forEach((item) =>
    item.addEventListener("click", () => {
      const ev = events.find((x) => x.id === item.dataset.eventId);
      if (ev && typeof showEventActions === "function") showEventActions(ev);
    })
  );
}

/* ---------------- Timetable overflow menu ---------------- */
/* is-open drives the button's quarter turn, and aria-expanded says the same
   thing to a screen reader. Both are set in one place so they cannot drift
   apart -- a rotated button that still reports itself closed is worse than
   no rotation at all. */
function setMoreMenu(open) {
  const menu = document.getElementById("tt-more-menu");
  const btn = document.getElementById("tt-more-btn");
  if (!menu) return;
  menu.classList.toggle("hidden", !open);
  btn?.classList.toggle("is-open", open);
  btn?.setAttribute("aria-expanded", String(open));
}

document.getElementById("tt-more-btn")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = document.getElementById("tt-more-menu");
  setMoreMenu(menu.classList.contains("hidden"));
});

document.addEventListener("click", (e) => {
  const menu = document.getElementById("tt-more-menu");
  if (menu && !menu.classList.contains("hidden") && !menu.contains(e.target)) {
    setMoreMenu(false);
  }
});

/* Escape closes it. A menu that can only be dismissed by clicking elsewhere
   is a trap for anyone driving the page from the keyboard. */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const menu = document.getElementById("tt-more-menu");
  if (menu && !menu.classList.contains("hidden")) {
    setMoreMenu(false);
    document.getElementById("tt-more-btn")?.focus();
  }
});

// Re-render when rotating between portrait and landscape.
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (typeof renderTimetable === "function" && document.getElementById("timetable-grid")) {
      renderTimetable();
    }
  }, 220);
});
