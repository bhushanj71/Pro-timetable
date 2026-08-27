/* The notification bell: one inbox, both kinds.

   The bell used to show work activity only while you were in Work mode, which
   meant an invitation or a progress update that arrived while you were looking
   at your timetable never appeared at all -- the badge stayed silent until you
   happened to switch. A notification you have to be in the right room to see
   is not a notification.

   So the bell now merges both feeds in both modes, newest first, with each row
   marked for where it came from. That is not a hole in the separation between
   Personal and Work: the two remain different *data*, shown on different
   pages: this is one person's own inbox, and they are entitled to see
   everything addressed to them in one place.

   On a phone the panel is a bottom sheet -- a dropdown pinned to a top-right
   icon is unreachable one-handed and, at forty items, taller than the screen.
*/

// Claimed before anything else runs, so app.js's personal renderer stands
// down in both modes rather than racing this one.
window.__notificationCentre = true;

const WK_ICON = {
  task_assigned: "📥", task_accepted: "✅", task_declined: "❌",
  task_started: "🚀", task_progress: "📊", task_completed: "🎉",
  task_all_completed: "🏁", task_due_soon: "⏰", task_due: "⏰",
  task_overdue: "🚨", assignment_reminder: "📥", task_comment: "💬",
  community_invite: "👥", invite_accepted: "🤝", invite_declined: "🙅",
  removed_from_community: "🚪", community_deleted: "🗑️", task_updated: "📝",
  task_unassigned: "📤",
};

function relTime(iso) {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} hr ago`;
  if (secs < 172800) return "yesterday";
  return `${Math.floor(secs / 86400)} days ago`;
}

function dayBucket(iso) {
  const d = new Date(iso), now = new Date();
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, now)) return "Today";
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (same(d, y)) return "Yesterday";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** Both feeds, merged and sorted newest-first. */
async function collectNotifications() {
  const [work, personal] = await Promise.all([
    apiFetch("/api/work/notifications").catch(() => ({ items: [], unread: 0 })),
    apiFetch("/api/reminders/notifications").catch(() => ({ items: [], unread: 0 })),
  ]);

  const rows = [
    ...work.items.map((n) => ({
      source: "work", id: n.id, kind: n.kind, title: n.title, body: n.body,
      at: n.at, read: n.read, taskId: n.task_id,
    })),
    ...personal.items.map((n) => ({
      source: "personal", id: n.id, kind: "reminder",
      title: n.title || "Reminder", body: null,
      at: n.reminder_datetime, read: n.is_read, taskId: null,
    })),
  ].sort((a, b) => new Date(b.at) - new Date(a.at));

  return {
    rows,
    unread: (work.unread || 0) + (personal.unread || 0),
    workUnread: work.unread || 0,
  };
}

function setBadges(unread, workUnread = null) {
  const badge = document.getElementById("notif-count");
  if (badge) {
    badge.textContent = unread > 9 ? "9+" : unread;
    badge.classList.toggle("hidden", unread === 0);
  }
  // The account button carries the work count, because the switcher it used
  // to sit on is now folded into that menu -- otherwise pending work would be
  // invisible from Personal mode.
  if (workUnread !== null) {
    for (const id of ["ps-work-badge", "um-work-count"]) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.textContent = workUnread > 9 ? "9+" : workUnread;
      el.classList.toggle("hidden", workUnread === 0);
    }
  }
}

async function renderNotificationCentre() {
  const panel = document.getElementById("notif-panel");
  if (!panel) return;

  let data;
  try {
    data = await collectNotifications();
  } catch (_) {
    return;
  }
  setBadges(data.unread, data.workUnread);

  let lastBucket = null;
  const rows = data.rows.map((n) => {
    const bucket = dayBucket(n.at);
    const header = bucket !== lastBucket ? `<div class="wn-day">${esc(bucket)}</div>` : "";
    lastBucket = bucket;
    const icon = n.source === "work" ? (WK_ICON[n.kind] || "💼") : "🔔";
    return `${header}
      <div class="wn-item${n.read ? "" : " unread"}"
           ${n.taskId ? `data-wn-task="${n.taskId}"` : ""}>
        <span class="wn-ico" aria-hidden="true">${icon}</span>
        <div class="wn-body">
          <div class="wn-title">${esc(n.title)}</div>
          ${n.body ? `<div class="wn-sub">${esc(n.body)}</div>` : ""}
          <div class="wn-when">
            <span class="wn-tag ${n.source}">${n.source === "work" ? "Work" : "Schedule"}</span>
            ${esc(relTime(n.at))}
          </div>
        </div>
        ${n.source === "work"
          ? `<button class="wn-x" data-wn-dismiss="${n.id}" aria-label="Dismiss">✕</button>` : ""}
      </div>`;
  }).join("");

  panel.innerHTML = `
    <div class="notif-head">
      <span>Notifications</span>
      <button type="button" class="notif-clear" id="wn-clear" ${data.rows.length ? "" : "disabled"}>Clear all</button>
    </div>
    ${rows || `<div class="notif-item notif-empty">Nothing yet. Task activity and reminders show up here.</div>`}`;
}

/* Mark everything seen, both feeds. */
async function markAllRead() {
  await Promise.all([
    apiFetch("/api/work/notifications/read", { method: "POST" }).catch(() => {}),
    apiFetch("/api/reminders/notifications/read", { method: "POST" }).catch(() => {}),
  ]);
  setBadges(0);
}

document.getElementById("notif-panel")?.addEventListener("click", async (e) => {
  const dismiss = e.target.closest("[data-wn-dismiss]");
  if (dismiss) {
    e.stopPropagation();
    try {
      await apiFetch(`/api/work/notifications/${dismiss.dataset.wnDismiss}`, { method: "DELETE" });
      renderNotificationCentre();
    } catch (_) { showToast("Could not dismiss that", "error"); }
    return;
  }

  if (e.target.id === "wn-clear") {
    try {
      await Promise.all([
        apiFetch("/api/work/notifications/clear", { method: "POST" }).catch(() => {}),
        apiFetch("/api/reminders/notifications/clear", { method: "POST" }).catch(() => {}),
      ]);
      renderNotificationCentre();
    } catch (_) { showToast("Could not clear notifications", "error"); }
    return;
  }

  // The point of the badge is to get somewhere, not just to be dismissed.
  const open = e.target.closest("[data-wn-task]");
  if (open) {
    document.getElementById("notif-panel").classList.add("hidden");
    if (typeof openTask === "function") openTask(open.dataset.wnTask);
    else window.location.href = "/work";
  }
});

document.getElementById("notif-bell")?.addEventListener("click", async () => {
  const panel = document.getElementById("notif-panel");
  if (panel?.classList.contains("hidden")) return;   // it was just closed
  await renderNotificationCentre();
  await markAllRead();
}, true);

/* Keep the badge current without becoming a polling client. Anything the user
   does themselves refreshes it immediately; this is the backstop for things
   other people do. */
/* Only where there is a session to poll for. This ran on the sign-in page
   too, where every call 401s -- noise in the console, and one more thing
   racing the form. */
if (document.getElementById("notif-bell")) {
  renderNotificationCentre();
  setInterval(renderNotificationCentre, 60_000);
}
window.addEventListener("work-updated", renderNotificationCentre);
window.addEventListener("schedule-updated", renderNotificationCentre);


/* ---------------- Account menu ----------------
   The name is the control: tapping it opens the account menu, which is where
   the choice between Personal and Work lives. */
const userMenu = () => document.getElementById("user-menu");

document.getElementById("user-menu-btn")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = userMenu();
  const open = menu.classList.toggle("hidden");
  e.currentTarget.setAttribute("aria-expanded", String(!open));
});

document.addEventListener("click", (e) => {
  const menu = userMenu();
  const btn = document.getElementById("user-menu-btn");
  if (menu && !menu.classList.contains("hidden") &&
      !menu.contains(e.target) && !btn?.contains(e.target)) {
    menu.classList.add("hidden");
    btn?.setAttribute("aria-expanded", "false");
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  userMenu()?.classList.add("hidden");
  document.getElementById("user-menu-btn")?.setAttribute("aria-expanded", "false");
});

// Logging out from the menu reuses the sidebar's handler rather than
// duplicating the sign-out logic.
document.getElementById("um-logout")?.addEventListener("click", () =>
  document.getElementById("logout-btn")?.click()
);
