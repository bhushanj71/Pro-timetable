/* Core client utilities shared across all pages: authenticated fetch,
   toasts, notification bell polling, global search, category colors. */

/* Escape before interpolating anything into innerHTML.

   Event titles, subjects and locations are stored verbatim and can arrive
   from an imported CSV as easily as from the professor's own typing, so a
   title of `<img src=x onerror=...>` would otherwise run script in their
   session with their cookie attached. */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const esc = escapeHtml;

const CATEGORY_COLORS = {
  lecture: "var(--cat-lecture)",
  lab: "var(--cat-lab)",
  meeting: "var(--cat-meeting)",
  project_review: "var(--cat-project_review)",
  examination: "var(--cat-examination)",
  personal: "var(--cat-personal)",
  research: "var(--cat-research)",
  deadline: "var(--cat-deadline)",
  conference: "var(--cat-conference)",
  fdp: "var(--cat-fdp)",
  workshop: "var(--cat-workshop)",
  other: "var(--cat-other)",
};

function categoryColor(type) {
  return CATEGORY_COLORS[type] || CATEGORY_COLORS.other;
}

/** Tinted background matching a category's accent, for tags and badges. */
function categorySoft(type) {
  const key = CATEGORY_COLORS[type] ? type : "other";
  return `var(--cat-${key}-soft)`;
}

const CATEGORY_LABELS = {
  lecture: "Lecture", lab: "Lab", meeting: "Meeting", project_review: "Review",
  examination: "Exam", personal: "Personal", research: "Research",
  deadline: "Deadline", conference: "Conference", fdp: "FDP",
  workshop: "Workshop", other: "Other",
};
function labelFor(type) {
  return CATEGORY_LABELS[type] || "Other";
}

// Set while signing out, so background polling that 401s mid-logout can't
// hijack the redirect and send the user to /login instead of the homepage.
let isSigningOut = false;

async function apiFetch(url, options = {}) {
  const opts = {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);

  const res = await fetch(url, opts);
  if (res.status === 401) {
    // A 401 from the sign-in and sign-up endpoints is a rejected credential,
    // not an expired session. Redirecting on those reloaded the very page the
    // error was about to be written to, so a wrong password produced a blank
    // form and no explanation.
    const isCredentialCheck = /\/api\/auth\/(login|register|token)\b/.test(url);
    // Nor is there anywhere to send someone who is already there: that is a
    // reload, and it wipes whatever the form was trying to say.
    const alreadyAtSignIn = /^\/(login|register)\b/.test(window.location.pathname);

    if (!isSigningOut && !isCredentialCheck && !alreadyAtSignIn) {
      window.location.href = "/login";
    }
    let message = "Not authenticated";
    try {
      const data = await res.json();
      if (data?.detail) message = data.detail;
    } catch (_) {
      // Body already read or not JSON; the default message stands.
    }
    throw new Error(message);
  }
  if (!res.ok) {
    let detail = "Something went wrong";
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  const contentType = res.headers.get("content-type") || "";
  return contentType.includes("application/json") ? res.json() : res.text();
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function fmtDate(iso) {
  return new Date(iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

/* ---------------- Modal keyboard behaviour ----------------

   Sixteen dialogs in this app and not one of them was a dialog as far as a
   keyboard or a screen reader was concerned: no role, no aria-modal, no
   Escape, and focus left sitting on whatever was behind the overlay. Tab
   walked the page underneath while the dialog covered it.

   Done once here, by watching for any .modal-backdrop losing its hidden
   class, rather than in each of the sixteen templates -- so a dialog added
   later gets the same behaviour without anyone remembering to wire it. */
const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled]):not([type=hidden])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

const modalReturnFocus = new WeakMap();

function focusablesIn(card) {
  return [...card.querySelectorAll(FOCUSABLE)].filter((el) => {
    const cs = getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden" && el.offsetParent !== null;
  });
}

function openModalA11y(backdrop) {
  const card = backdrop.querySelector(".modal-card");
  if (!card) return;

  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  if (!card.getAttribute("aria-label") && !card.getAttribute("aria-labelledby")) {
    const heading = card.querySelector("h1,h2,h3,h4");
    if (heading) {
      if (!heading.id) heading.id = `modal-title-${Math.random().toString(36).slice(2, 8)}`;
      card.setAttribute("aria-labelledby", heading.id);
    }
  }

  modalReturnFocus.set(backdrop, document.activeElement);
  // The first control, not the card: someone arriving here is going to act,
  // and landing on the container costs them a keypress every time.
  const first = focusablesIn(card)[0];
  (first || card).focus({ preventScroll: true });
  if (!first) card.setAttribute("tabindex", "-1");
}

function closeModalA11y(backdrop) {
  const previous = modalReturnFocus.get(backdrop);
  modalReturnFocus.delete(backdrop);
  // Back where they were. Without this, focus falls to the top of the
  // document and a keyboard user has to walk the whole page again.
  if (previous && document.contains(previous) && previous.offsetParent !== null) {
    previous.focus({ preventScroll: true });
  }
}

const openBackdrops = () =>
  [...document.querySelectorAll(".modal-backdrop")].filter((m) => !m.classList.contains("hidden"));

document.addEventListener("keydown", (e) => {
  const open = openBackdrops();
  if (!open.length) return;
  const backdrop = open[open.length - 1];   // the topmost one
  const card = backdrop.querySelector(".modal-card");
  if (!card) return;

  // Escape is handled by the overlay closer further down, which already owned
  // it before this layer existed. Two handlers closing the same dialog is one
  // too many places to remember.
  if (e.key !== "Tab") return;
  const items = focusablesIn(card);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];

  // Wrap at both ends, so Tab cannot walk out into the page behind.
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  } else if (!card.contains(document.activeElement)) {
    e.preventDefault();
    first.focus();
  }
});

/* Modals are shown by toggling one class, from a dozen different scripts.
   Watching the attribute is what makes this work for all of them without
   every caller having to announce itself. */
(function watchModals() {
  const observer = new MutationObserver((records) => {
    for (const r of records) {
      const el = r.target;
      if (!el.classList?.contains("modal-backdrop")) continue;
      const hidden = el.classList.contains("hidden");
      const wasHidden = r.oldValue?.includes("hidden");
      if (wasHidden && !hidden) openModalA11y(el);
      else if (!wasHidden && hidden) closeModalA11y(el);
    }
  });
  observer.observe(document.body, {
    attributes: true, attributeFilter: ["class"],
    attributeOldValue: true, subtree: true,
  });
  // Anything already open at load, e.g. onboarding.
  openBackdrops().forEach(openModalA11y);
})();

/* ---------------- Department pickers ----------------
   Departments and administrative posts under separate headings.

   They are the same kind of row in the database, because both answer "which
   part of the college is this person in", and every membership, filter and
   directory lookup already runs on a department id. They are not the same kind
   of thing to a reader, though: a flat list puts Registrar between two
   engineering departments, where nobody scanning for their own department
   expects to find it.

   Lives here rather than in work.js or profile.js because both pickers need
   it, and two copies of a list-rendering rule drift the moment one is
   changed. */
function departmentOptions(departments) {
  const options = (list) =>
    list.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
  const academic = departments.filter((d) => (d.kind || "academic") !== "office");
  const offices = departments.filter((d) => (d.kind || "academic") === "office");

  // No heading when there is only one group. An optgroup of one labelled
  // category is a label with nothing to distinguish it from.
  if (!offices.length) return options(academic);
  if (!academic.length) return options(offices);
  return (
    `<optgroup label="Departments">${options(academic)}</optgroup>` +
    `<optgroup label="Administration">${options(offices)}</optgroup>`
  );
}

/* Any container marked is-loading-block gets the animation while it waits.

   Server-rendered HTML cannot call loaderMarkup, so the markup is filled in
   here on load. It means a template says only "this is loading" and does not
   also carry fifteen spans it would have to keep in step with the stylesheet.
   Whatever renders into the container replaces it, exactly as the placeholder
   text it stands in for was replaced. */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".is-loading-block").forEach((el) => {
    el.innerHTML = loaderMarkup("sm", "Loading");
  });
});

/* ---------------- Route veil ----------------
   Signing in and signing out both end in a full page load. Between the click
   and the new document there is nothing on screen saying the click landed,
   which is exactly long enough for someone to press the button again. This
   covers that gap and locks the form underneath at the same time. */
/* The loading animation's markup, in one place.

   Fifteen spans is a lot to type, and typing it in five places is five places
   to get the count wrong -- the colour ramp is keyed to nth-child, so fourteen
   spans silently shifts every colour by one.

   size: "" | "sm" | "lg". */
function loaderMarkup(size = "", label = "") {
  const cls = "loader" + (size ? ` loader-${size}` : "");
  const dots = '<span></span>'.repeat(15);
  const body = `<div class="${cls}" aria-hidden="true">${dots}</div>` +
    (label ? `<span class="loader-label">${esc(label)}</span>` : "");
  // role=status on the wrapper, and the animation itself hidden from the
  // accessibility tree: fifteen empty spans announce nothing useful, and the
  // label is what actually says what is happening.
  return `<div role="status" aria-live="polite" aria-label="${esc(label || "Loading")}">${body}</div>`;
}

function showRouteVeil(label, sub) {
  let veil = document.getElementById("route-veil");
  if (veil) return veil;

  /* The page leaves the way it arrived, run backwards: the circle closes over
     it while the next document is fetched, and the next document opens one
     again -- so a navigation is one movement rather than a spinner and then a
     page.

     It closes from wherever the press landed, which is what makes the
     movement feel caused rather than merely scheduled, and that point is
     handed to the next page so its circle opens from the same spot.

     The label rides inside it rather than on a veil of its own: two
     full-screen layers in the same colour is one layer and a bug waiting to
     be found. */
  veil = document.createElement("div");
  veil.id = "route-veil";
  veil.className = "page-wipe page-wipe-close";
  // polite, not assertive: this is progress, not an alert.
  veil.setAttribute("role", "status");
  veil.setAttribute("aria-live", "polite");
  const from = window.__wipeOrigin;
  if (from) {
    veil.style.setProperty("--wipe-x", `${from.x}px`);
    veil.style.setProperty("--wipe-y", `${from.y}px`);
    try {
      sessionStorage.setItem("ps-wipe", `${from.x},${from.y}`);
    } catch (_) {}
  }
  veil.innerHTML =
    `<span class="page-wipe-label">${esc(label)}${sub ? ` — ${esc(sub)}` : ""}</span>`;
  document.body.appendChild(veil);
  return veil;
}

function hideRouteVeil() {
  document.getElementById("route-veil")?.remove();
}

/* A cached page restored with the back button keeps the DOM it was unloaded
   with -- including a veil that has nothing left to wait for. */
window.addEventListener("pageshow", (e) => { if (e.persisted) hideRouteVeil(); });

/* Where the last press landed, so a navigation's circle closes from the point
   that caused it. Captured in the capture phase and on the document, because
   by the time a link handler runs the coordinates are somebody else's
   problem. */
window.__wipeOrigin = null;
document.addEventListener("pointerdown", (e) => {
  window.__wipeOrigin = { x: Math.round(e.clientX), y: Math.round(e.clientY) };
}, true);

/* The arriving page opens from wherever the departing one was pressed. */
(() => {
  const wipe = document.getElementById("page-wipe");
  if (!wipe) return;
  try {
    const saved = sessionStorage.getItem("ps-wipe");
    if (saved) {
      const [x, y] = saved.split(",").map(Number);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        wipe.style.setProperty("--wipe-x", `${x}px`);
        wipe.style.setProperty("--wipe-y", `${y}px`);
      }
      // Read once. A stale point would open the next page from wherever an
      // unrelated link was pressed several navigations ago.
      sessionStorage.removeItem("ps-wipe");
    }
  } catch (_) {}

  /* Belt and braces. The opening is pure CSS precisely so it cannot depend on
     this file running -- but the failure CSS cannot cover for is animations
     not running at all, and the overlay rests in the covering position, so
     that failure is a blank screen rather than a missing flourish. */
  setTimeout(() => wipe.remove(), 2000);
})();

/* ---------------- Navigation feedback ----------------
   Every item in the tab bar and the sidebar is an ordinary link to a
   server-rendered page. Between the tap and the new document painting there
   was nothing on screen at all: no pressed state, no spinner, nothing. On
   localhost that gap is about 95ms and invisible. Over mobile data it is far
   longer, and a cold start on the host is several seconds of a screen that
   looks frozen -- which is when people tap a second time.

   Two signals, deliberately on different clocks:

   The tapped item shows a spinner immediately, in place of its own icon. It
   is the tapped item that says it was tapped, which is more precise than a
   general "loading" and is the part that stops the second tap.

   The full veil waits. Showing it at once would mean covering the whole
   screen for 95ms on every navigation -- a flash that reads as a glitch and
   is worse than no feedback. It appears only if the page is genuinely slow,
   so on a fast connection it never shows at all. */
const NAV_VEIL_DELAY = 400;
let navPending = null;

function navLabel(link) {
  // The label without its icon or badge: "🏠 Home" is not what to say.
  const copy = link.cloneNode(true);
  // Both badge classes: the sidebar uses .nav-badge and the tab bar .bn-badge,
  // and missing one turns "Reminders" into "Reminders 0" in the veil. The
  // count is still in the text even when the badge is hidden by CSS.
  copy.querySelectorAll(".bn-ico, .nav-ico, .bn-badge, .nav-badge").forEach((n) => n.remove());
  return copy.textContent.trim();
}

function beginNavigation(link) {
  if (navPending) return;
  link.classList.add("is-navigating");
  const label = navLabel(link);
  navPending = {
    link,
    timer: setTimeout(() => {
      showRouteVeil(label ? `Opening ${label}…` : "Loading…");
    }, NAV_VEIL_DELAY),
  };
}

function cancelNavigation() {
  if (!navPending) return;
  clearTimeout(navPending.timer);
  navPending.link.classList.remove("is-navigating");
  navPending = null;
  hideRouteVeil();
}

document.addEventListener("click", (e) => {
  const link = e.target.closest(".bn-item[href], .sidebar .nav-link[href]");
  if (!link) return;

  // A modified click opens a new tab, so this document is going nowhere.
  // Showing it a spinner would leave one spinning forever.
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey ||
      e.shiftKey || e.altKey || link.target === "_blank") return;

  // Already here. There is no navigation to wait for, and a veil for a page
  // that never reloads is the flash this whole delay exists to avoid.
  const href = new URL(link.getAttribute("href"), location.href);
  if (href.pathname === location.pathname && href.origin === location.origin) return;

  beginNavigation(link);
});

/* A restored page keeps the DOM it was unloaded with, spinner included. */
window.addEventListener("pageshow", (e) => { if (e.persisted) cancelNavigation(); });

/* If the navigation is abandoned -- a download, a refused request, a link
   that turns out to be a no-op -- the spinner must not outlive it. */
window.addEventListener("pagehide", cancelNavigation);

/* ---------------- Logout ---------------- */
document.getElementById("logout-btn")?.addEventListener("click", async () => {
  isSigningOut = true;
  showRouteVeil("Signing you out…", "See you soon.");
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch (_) {
    // The cookie may already be gone; still send them to the homepage rather
    // than leaving them on a page they can no longer load.
  }
  // replace() so Back doesn't return to the signed-in page they just left.
  window.location.replace("/");
});

/* ---------------- Notification bell ---------------- */

async function pollNotifications() {
  // The bell belongs to the unified notification centre, which merges this
  // feed with work activity. Two renderers writing to one panel meant
  // whichever ran last won, and the badge disagreed with the list.
  if (window.__notificationCentre) return;
  try {
    const data = await apiFetch("/api/reminders/notifications");
    const count = document.getElementById("notif-count");
    const panel = document.getElementById("notif-panel");
    if (!count || !panel) return;

    // The badge tracks *unread*, not merely delivered — otherwise it could
    // never be cleared.
    if (data.unread > 0) {
      count.textContent = data.unread > 9 ? "9+" : data.unread;
      count.classList.remove("hidden");
    } else {
      count.classList.add("hidden");
    }

    const head = `<div class="notif-head">
        <span>Notifications</span>
        <button type="button" class="notif-clear" id="notif-clear"
                ${data.items.length ? "" : "disabled"}>Clear all</button>
      </div>`;

    panel.innerHTML =
      head +
      (data.items.length
        ? data.items
            .map(
              (n) => `<div class="notif-item${n.is_read ? "" : " unread"}">
              🔔 ${esc(n.title || "Reminder")}
              <br><small>${fmtDate(n.reminder_datetime)} · ${fmtTime(n.reminder_datetime)}</small>
            </div>`
            )
            .join("")
        : `<div class="notif-item notif-empty">No notifications yet.</div>`);
  } catch (_) {
    /* silent: polling shouldn't interrupt the page */
  }
}

/* ---------------- Refresh ----------------
   Pages that can rebuild themselves from data register a function here and
   get refreshed in place: no white flash, and the scroll position survives.
   Everything else falls back to a real reload, which is what the button says
   it does and is always correct. */
const pageRefreshers = new Set();

function registerRefresh(fn) {
  if (typeof fn === "function") pageRefreshers.add(fn);
}

async function refreshNow() {
  const btn = document.getElementById("refresh-btn");
  if (btn?.classList.contains("is-spinning")) return;   // one at a time
  btn?.classList.add("is-spinning");
  btn?.setAttribute("aria-busy", "true");

  // Whatever happens next must not be served from the cache this button
  // exists to get past.
  if (typeof clearCache === "function") clearCache();

  if (!pageRefreshers.size) {
    window.location.reload();
    return;                                  // the spinner leaves with the page
  }

  try {
    // allSettled, not all: one failing panel should not stop the others from
    // updating, and the button has to come back either way.
    await Promise.allSettled([...pageRefreshers].map((fn) => fn()));
    if (document.getElementById("notif-bell")) {
      await (window.__notificationCentre
        ? window.renderNotificationCentre?.()
        : pollNotifications());
    }
    showToast("Up to date", "success");
  } catch (_) {
    showToast("Could not refresh", "error");
  } finally {
    // A spin that stops mid-turn reads as a glitch, so it is left to finish
    // the rotation it is in.
    const stop = () => {
      btn?.classList.remove("is-spinning");
      btn?.removeAttribute("aria-busy");
    };
    btn ? btn.addEventListener("animationiteration", stop, { once: true }) : stop();
    setTimeout(stop, 1200);   // backstop: no animation, or none running
  }
}

document.getElementById("refresh-btn")?.addEventListener("click", refreshNow);

/* ---------------- Swipe the notification sheet away ----------------
   The panel drops from the bell at the top, so it is dismissed by pushing it
   back where it came from: upwards.

   The scroll conflict mirrors the bottom-sheet case and is worse. For a sheet
   at the bottom, "drag down" is free the moment the list is at the top, which
   is where it starts. For one at the top, "drag up" is only free once the
   list is at its END -- and a list of forty notifications is almost never
   scrolled to the end. Requiring that would make the gesture unreachable.

   So the handle earns its keep: a drag starting on the strip along the bottom
   edge dismisses whatever the list is doing, and a drag starting anywhere
   else only takes over once there is nothing left to scroll. Tapping outside
   and the bell itself both still close it, so the gesture is never the only
   way out. */
const SHEET_DISMISS_FRACTION = 0.28;   // of the sheet's own height
const SHEET_FLICK_VELOCITY = 0.5;      // px per ms, measured over the last move
// A flick still has to travel. Without this floor, velocity taken over a very
// short gesture is enormous -- a small nudge read as a flick and threw the
// sheet away when it should have snapped back.
const SHEET_FLICK_MIN_DISTANCE = 56;
// How deep the grab strip along the bottom edge is. Generous, because it is
// the one place the gesture always works.
const SHEET_HANDLE_DEPTH = 56;

function isSheetMode() {
  return window.matchMedia("(max-width: 768px)").matches;
}

function resetSheet(panel) {
  panel.classList.remove("is-dragging", "is-dismissing", "is-settling");
  panel.style.transform = "";
}

function closeNotifPanel(panel, { animate = false } = {}) {
  if (!animate) {
    panel.classList.add("hidden");
    resetSheet(panel);
    return;
  }
  panel.classList.remove("is-dragging");
  panel.classList.add("is-dismissing");
  // transitionend is not guaranteed (a display change mid-flight swallows it),
  // so the timeout is the one that actually closes it and the event only gets
  // there first.
  const finish = () => {
    panel.classList.add("hidden");
    resetSheet(panel);
  };
  panel.addEventListener("transitionend", finish, { once: true });
  setTimeout(finish, 260);
}

(function enableSheetSwipe() {
  const panel = document.getElementById("notif-panel");
  if (!panel) return;

  let startY = 0, delta = 0;
  // Velocity comes from the last pair of samples, not from the whole gesture:
  // someone who drags slowly and then flicks has flicked, and someone who
  // drags fast and then holds still has not.
  let lastY = 0, lastAt = 0, velocity = 0;
  let tracking = false, dragging = false;

  panel.addEventListener("touchstart", (e) => {
    if (!isSheetMode() || e.touches.length !== 1) return;
    if (panel.classList.contains("is-dismissing")) return;

    const touch = e.touches[0];
    startY = lastY = touch.clientY;
    lastAt = Date.now();
    velocity = 0;
    delta = 0;
    dragging = false;

    const box = panel.getBoundingClientRect();
    const onHandle = touch.clientY >= box.bottom - SHEET_HANDLE_DEPTH;
    const atEnd = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 1;
    // Decided once, at the start: re-checking mid-gesture would let a drag
    // begin the instant the list happened to reach its end.
    tracking = onHandle || atEnd;
    panel.classList.remove("is-settling");
  }, { passive: true });

  panel.addEventListener("touchmove", (e) => {
    if (!tracking || !isSheetMode()) return;
    const y = e.touches[0].clientY;
    const now = Date.now();
    const dt = now - lastAt;
    if (dt > 0) velocity = (y - lastY) / dt;
    lastY = y;
    lastAt = now;
    delta = y - startY;   // negative is upward

    if (delta >= 0) {
      // Downward: hand it back to the scroller and do not take it again until
      // the next touch.
      if (dragging) {
        dragging = false;
        panel.classList.remove("is-dragging");
        panel.style.transform = "";
      }
      tracking = false;
      return;
    }
    // A few pixels of slop, so a tap with a shaky thumb is still a tap.
    if (!dragging && delta > -8) return;

    if (!dragging) {
      dragging = true;
      panel.classList.add("is-dragging");
    }
    // Stops the page behind the sheet scrolling with the finger.
    e.preventDefault();
    panel.style.transform = `translateY(${delta}px)`;
  }, { passive: false });

  const release = () => {
    if (!dragging) { tracking = false; return; }
    dragging = false;
    tracking = false;
    panel.classList.remove("is-dragging");

    const travelled = -delta;                 // upward distance
    const upwardVelocity = -velocity;
    const far = travelled > panel.offsetHeight * SHEET_DISMISS_FRACTION;
    // A finger lifted after pausing has velocity ~0, so a long slow drag is
    // carried by `far` and a short fast one by `flicked`.
    const flicked = upwardVelocity > SHEET_FLICK_VELOCITY && travelled > SHEET_FLICK_MIN_DISTANCE;

    if (far || flicked) {
      closeNotifPanel(panel, { animate: true });
    } else {
      // Snap back, then take the class off so the next open animates normally.
      panel.classList.add("is-settling");
      panel.style.transform = "";
      setTimeout(() => panel.classList.remove("is-settling"), 240);
    }
  };

  panel.addEventListener("touchend", release);
  panel.addEventListener("touchcancel", release);
})();

document.getElementById("notif-bell")?.addEventListener("click", async () => {
  const panel = document.getElementById("notif-panel");
  const opening = panel?.classList.contains("hidden");
  // A sheet that was swiped away still carries that gesture's transform;
  // reopening without clearing it would place the panel off-screen.
  if (panel) resetSheet(panel);
  panel?.classList.toggle("hidden");

  // The notification centre fills the panel and marks both feeds read.
  if (window.__notificationCentre) return;
  if (!opening) return;
  // Clear the badge immediately, then persist — the click is the "read".
  document.getElementById("notif-count")?.classList.add("hidden");
  try {
    await apiFetch("/api/reminders/notifications/read", { method: "POST" });
    await pollNotifications();
  } catch (_) {}
});

// Delegated: pollNotifications replaces the panel's contents wholesale, so a
// listener bound to the button itself would be thrown away on every refresh.
document.getElementById("notif-panel")?.addEventListener("click", async (e) => {
  const btn = e.target.closest("#notif-clear");
  if (!btn) return;

  setButtonLoading(btn, true);
  try {
    const { cleared } = await apiFetch("/api/reminders/notifications/clear", { method: "POST" });
    await pollNotifications();
    showToast(cleared ? `Cleared ${cleared} notification${cleared === 1 ? "" : "s"}` : "Nothing to clear", "success");
  } catch (_) {
    showToast("Could not clear notifications", "error");
    setButtonLoading(btn, false);
  }
});

document.addEventListener("click", (e) => {
  const panel = document.getElementById("notif-panel");
  const bell = document.getElementById("notif-bell");
  if (panel && !panel.classList.contains("hidden") && !panel.contains(e.target) && !bell?.contains(e.target)) {
    closeNotifPanel(panel);
  }
});

/* ---------------- Polling ----------------

   A poll that costs nothing while nobody is looking.

   Two notification requests a minute is three thousand requests a second at a
   hundred thousand signed-in tabs, and most of those tabs are behind
   something else. A hidden tab cannot show a badge it has just refreshed, so
   the request, the six queries behind it and the render are all thrown away.
   This stops the timer while the page is hidden and refreshes once on the way
   back, which is exactly when the feed is most likely to be stale.

   The period is spread by +/-15% per tab. Without that, every tab opened
   after a deploy wakes in the same second and stays in step for as long as it
   is open, so the load arrives as a spike on the minute rather than as a
   rate. */
function startPoll(fn, periodMs) {
  /* Flicking between two tabs should not be a way to hammer the server, so a
     return to visibility refreshes only if the answer could have changed. */
  const MIN_GAP_MS = 5000;
  let timer = null;
  let last = 0;

  function stop() {
    if (timer) { clearTimeout(timer); timer = null; }
  }

  /* A rescheduling setTimeout rather than setInterval: when a response is
     slow, the next call should be pushed out, not stack up behind it. */
  function schedule() {
    stop();
    timer = setTimeout(run, periodMs * (0.85 + Math.random() * 0.3));
  }

  async function run() {
    if (document.visibilityState !== "visible") { stop(); return; }
    last = Date.now();
    try { await fn(); } catch (_) {}
    schedule();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") { stop(); return; }
    if (Date.now() - last >= MIN_GAP_MS) run();
    else schedule();
  });
  window.addEventListener("pagehide", stop);

  schedule();
  return stop;
}
window.startPoll = startPoll;

/* The bell is polled by work-notifications.js, which owns the unified centre
   and is loaded on every signed-in page. There used to be a second timer here
   as well. It did nothing but cost: this file runs first, so the flag that
   makes pollNotifications defer to the centre is not set yet when this line
   is reached -- which meant one duplicate fetch on every page load -- and
   every tick after that returned immediately, forever. */

/* ---------------- Skeletons ----------------

   Shown only when there is nothing to show yet. A panel that already holds
   data keeps it while refreshing: replacing real content with grey bars is a
   downgrade, not a loading state. */

const skeleton = {
  // Mirrors .ue-item as it actually renders: badge, title, two meta lines,
  // and the action pills that wrap onto their own row in a narrow card. A
  // skeleton shorter than the content it stands in for just moves the jump
  // rather than removing it.
  rows: (n = 3) =>
    Array.from({ length: n }, () => `
      <div class="sk-row">
        <div class="sk sk-badge"></div>
        <div class="sk-body">
          <div class="sk sk-line medium"></div>
          <div class="sk sk-line short"></div>
          <div class="sk sk-line short"></div>
        </div>
        <div class="sk-row-actions">
          <div class="sk sk-pill"></div>
          <div class="sk sk-pill" style="width:104px"></div>
        </div>
      </div>`).join(""),

  timeline: (n = 3) =>
    Array.from({ length: n }, () => `
      <div class="sk-timeline-row">
        <div class="sk sk-line sk-time"></div>
        <div style="flex:1">
          <div class="sk sk-line medium"></div>
          <div class="sk sk-line short"></div>
        </div>
      </div>`).join(""),

  list: (n = 4) =>
    Array.from({ length: n }, () => `<div class="sk sk-line long" style="height:34px;margin-bottom:10px"></div>`).join(""),

  grid: (n = 24) =>
    `<div class="sk-grid">${Array.from({ length: n }, () => `<div class="sk sk-cell"></div>`).join("")}</div>`,
};

/* ---------------- Loading helpers ---------------- */

// Nothing renders for the first 300ms: most requests finish inside that, and
// flashing a loader for them reads as jank rather than feedback.
const LOADER_DELAY_MS = 300;

/** Markup for the five-dot loader. */
function dotsMarkup(extraClass = "") {
  /* Kept as the name every block-level loading state already calls, now
     drawing the real animation. Renaming it would have meant touching every
     call site to change nothing about what they wanted -- a loading mark.

     .dots5 stays in the stylesheet: it is still what a button spinner uses,
     where fifteen ellipses would be a smudge. */
  return loaderMarkup("sm");
}


/** Toggle a button's spinner. Also disables it, which prevents the
 *  double-submits that previously created duplicate events. */
function setButtonLoading(btn, isLoading, loadingLabel) {
  if (!btn) return;

  if (isLoading) {
    if (!btn.dataset.originalHtml) btn.dataset.originalHtml = btn.innerHTML;
    // Lock the width so the layout doesn't jump when the label is replaced.
    btn.style.minWidth = `${btn.offsetWidth}px`;
    btn.disabled = true;

    // Defer the visual state: a fast action shouldn't flash a loader.
    btn._loadTimer = setTimeout(() => {
      if (loadingLabel) btn.innerHTML = loadingLabel;
      btn.classList.add("is-loading");
      btn.insertAdjacentHTML("beforeend", dotsMarkup("dots5-sm"));
    }, LOADER_DELAY_MS);
  } else {
    clearTimeout(btn._loadTimer);
    btn.classList.remove("is-loading");
    btn.disabled = false;
    btn.querySelector(".dots5")?.remove();
    if (btn.dataset.originalHtml) {
      btn.innerHTML = btn.dataset.originalHtml;
      delete btn.dataset.originalHtml;
    }
    btn.style.minWidth = "";
  }
}

/** Render shimmer placeholder rows into a container while data loads. */
function showSkeleton(container, rows = 3, variant = "rows") {
  if (!container) return;
  // A panel that already holds real content keeps it while refreshing:
  // replacing a rendered schedule with grey bars is a downgrade, not a
  // loading state.
  if (container.dataset.loaded === "1") return;
  const build = skeleton[variant] || skeleton.rows;
  container.innerHTML = build(rows);
}

/** Mark a container as populated, so later refreshes don't flash skeletons. */
function markLoaded(container) {
  if (container) container.dataset.loaded = "1";
}

/** Cover a container with a spinner overlay. Returns a function to remove it. */
function showOverlay(container, label = "Loading…") {
  if (!container) return () => {};
  container.classList.add("loading-host");
  let el = null;
  const timer = setTimeout(() => {
    el = document.createElement("div");
    el.className = "loading-overlay";
    el.innerHTML = `${dotsMarkup()}<span>${label}</span>`;
    container.appendChild(el);
  }, LOADER_DELAY_MS);

  return () => {
    clearTimeout(timer);
    el?.remove();
    container.classList.remove("loading-host");
  };
}

/**
 * "Working on it" panel with rotating status messages.
 */
function startProgress(container, messages, options = {}) {
  if (!container) return { stop() {} };
  const list = messages.length ? messages : ["Working..."];
  let index = 0;
  let msgTimer = null;
  let shown = false;

  const showTimer = setTimeout(() => {
    shown = true;
    container.innerHTML = `
      <div class="ai-result-card">
        <div class="ai-thinking">
          ${dotsMarkup()}
          <span class="ai-thinking-text" id="progress-text">${list[0]}</span>
        </div>
      </div>`;

    const textEl = container.querySelector("#progress-text");
    msgTimer = setInterval(() => {
      index = (index + 1) % list.length;
      if (textEl) textEl.textContent = list[index];
    }, 2200);
  }, options.delay ?? LOADER_DELAY_MS);

  return {
    stop() {
      clearTimeout(showTimer);
      if (msgTimer) clearInterval(msgTimer);
      // Clear the panel only if we actually drew it; otherwise the caller's
      // own result rendering must not be wiped.
      if (shown && options.clearOnStop) container.innerHTML = "";
    },
    get visible() {
      return shown;
    },
  };
}


/* ---------------- Shell interactions ---------------- */

// Mobile sidebar
const _sidebar = document.getElementById("sidebar");
const _scrim = document.getElementById("scrim");
function toggleSidebar(open) {
  if (!_sidebar) return;
  const show = open ?? !_sidebar.classList.contains("open");
  _sidebar.classList.toggle("open", show);
  _scrim?.classList.toggle("hidden", !show);
  document.body.style.overflow = show ? "hidden" : "";
}
document.getElementById("menu-btn")?.addEventListener("click", () => toggleSidebar());
_scrim?.addEventListener("click", () => toggleSidebar(false));
// Navigating on mobile should close the drawer behind you.
_sidebar?.querySelectorAll("a").forEach((a) =>
  a.addEventListener("click", () => window.innerWidth <= 768 && toggleSidebar(false))
);

// Elevate the topbar once the page scrolls beneath it, and get it out of the
// way while the page is being read.
//
// The bar used to be pinned: always there, over the page, costing 65px of a
// short screen on every screenful. Taking it out of the flow entirely fixed
// that and cost something else -- the bell, the theme toggle and the account
// menu became a scroll to the top of the page rather than a control.
//
// So: reading downwards tucks it away, and any upward movement brings it
// straight back. The controls are one gesture away, and the page still gets
// the whole screen while it is being read.
const _topbar = document.getElementById("topbar");
if (_topbar) {
  // Below this there is nothing to have scrolled past yet, and a bar that
  // vanishes at the top of a page reads as a glitch rather than as room.
  const TUCK_BELOW = 80;
  // A trembling finger, and the rubber-band bounce at either end of a phone
  // scroll, are not a change of direction. Without this the bar flickers.
  const DEADBAND = 4;

  let lastY = window.scrollY;

  // Both menus live inside the bar, so tucking it while one is open takes the
  // open menu off the screen with it.
  const menuOpen = () =>
    !!document.querySelector("#user-menu:not(.hidden), #theme-menu:not(.hidden)");

  const onScroll = () => {
    // Clamped: iOS reports a negative scrollY while rubber-banding at the top,
    // which reads as "going up" from zero and is meaningless here.
    const y = Math.max(0, window.scrollY);
    _topbar.classList.toggle("is-stuck", y > 6);

    const moved = y - lastY;
    if (Math.abs(moved) < DEADBAND) return;
    lastY = y;

    _topbar.classList.toggle("is-tucked", moved > 0 && y > TUCK_BELOW && !menuOpen());
  };

  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}

// Escape closes any open overlay.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  document.getElementById("notif-panel")?.classList.add("hidden");
  document.getElementById("theme-menu")?.classList.add("hidden");
  // Not the ones the server also enforces. Escape used to close the
  // work-profile panel too, which only moved the refusal to the next action:
  // a 428 later instead of a choice now.
  document
    .querySelectorAll('.modal-backdrop:not(.hidden):not([data-mandatory="true"])')
    .forEach((m) => m.classList.add("hidden"));
  if (window.innerWidth <= 768) toggleSidebar(false);
});

// Reflect pending reminders on the sidebar badge too.
async function refreshSidebarBadges() {
  try {
    const rem = await apiFetch("/api/reminders");
    const badge = document.getElementById("nav-reminder-count");
    if (badge) {
      const pending = rem.filter((r) => !r.is_sent).length;
      badge.textContent = pending;
      badge.classList.toggle("hidden", pending === 0);
    }
  } catch (_) {}
}
if (document.getElementById("nav-reminder-count")) refreshSidebarBadges();
