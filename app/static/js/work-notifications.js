/* Work notification centre.

   Reuses the existing bell rather than adding a second one: in Work mode the
   bell shows work activity, in Personal mode it shows schedule reminders. One
   control, one badge, and no chance of a professor watching the wrong one.

   On a phone the panel becomes a bottom sheet. A dropdown anchored to a
   top-right icon is unreachable one-handed and, at 40 items, taller than the
   screen. */

const WK_ICON = {
  task_assigned: "📥", task_accepted: "✅", task_declined: "❌",
  task_started: "🚀", task_progress: "📊", task_completed: "🎉",
  task_all_completed: "🏁", task_due_soon: "⏰", task_due: "⏰",
  task_overdue: "🚨", assignment_reminder: "📥", task_comment: "💬",
  community_invite: "👥", invite_accepted: "🤝", invite_declined: "🙅",
  removed_from_community: "🚪", task_updated: "📝",
};

const isWorkMode = () => document.body.dataset.profile === "work";

function relTime(iso) {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} hr ago`;
  if (secs < 172800) return "yesterday";
  return `${Math.floor(secs / 86400)} days ago`;
}

/** Group by day, because a flat list of 40 gives no sense of when. */
function dayBucket(iso) {
  const d = new Date(iso), now = new Date();
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  if (sameDay(d, now)) return "Today";
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (sameDay(d, y)) return "Yesterday";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

async function renderWorkNotifications() {
  const panel = document.getElementById("notif-panel");
  const badge = document.getElementById("notif-count");
  if (!panel) return;

  let data;
  try {
    data = await apiFetch("/api/work/notifications");
  } catch (_) {
    return;
  }

  if (badge) {
    badge.textContent = data.unread > 9 ? "9+" : data.unread;
    badge.classList.toggle("hidden", data.unread === 0);
  }
  // The Work tab in the switcher carries the same count, so the badge is
  // visible from Personal mode too.
  const tab = document.getElementById("ps-work-badge");
  if (tab) {
    tab.textContent = data.unread > 9 ? "9+" : data.unread;
    tab.classList.toggle("hidden", data.unread === 0);
  }

  let lastBucket = null;
  const rows = data.items.map((n) => {
    const bucket = dayBucket(n.at);
    const header = bucket !== lastBucket ? `<div class="wn-day">${esc(bucket)}</div>` : "";
    lastBucket = bucket;
    return `${header}
      <div class="wn-item${n.read ? "" : " unread"}" ${n.task_id ? `data-wn-task="${n.task_id}"` : ""}>
        <span class="wn-ico" aria-hidden="true">${WK_ICON[n.kind] || "🔔"}</span>
        <div class="wn-body">
          <div class="wn-title">${esc(n.title)}</div>
          ${n.body ? `<div class="wn-sub">${esc(n.body)}</div>` : ""}
          <div class="wn-when">${esc(relTime(n.at))}</div>
        </div>
        <button class="wn-x" data-wn-dismiss="${n.id}" aria-label="Dismiss">✕</button>
      </div>`;
  }).join("");

  panel.innerHTML = `
    <div class="notif-head">
      <span>💼 Work activity</span>
      <button type="button" class="notif-clear" id="wn-clear" ${data.items.length ? "" : "disabled"}>Clear all</button>
    </div>
    ${rows || `<div class="notif-item notif-empty">Nothing yet. Task activity shows up here.</div>`}`;
}

/* Tapping a notification opens the task it is about -- the whole point of the
   badge is to get somewhere, not just to be dismissed. */
document.getElementById("notif-panel")?.addEventListener("click", async (e) => {
  const dismiss = e.target.closest("[data-wn-dismiss]");
  if (dismiss) {
    e.stopPropagation();
    try {
      await apiFetch(`/api/work/notifications/${dismiss.dataset.wnDismiss}`, { method: "DELETE" });
      renderWorkNotifications();
    } catch (_) { showToast("Could not dismiss that", "error"); }
    return;
  }

  if (e.target.id === "wn-clear") {
    try {
      await apiFetch("/api/work/notifications/clear", { method: "POST" });
      renderWorkNotifications();
    } catch (_) { showToast("Could not clear notifications", "error"); }
    return;
  }

  const open = e.target.closest("[data-wn-task]");
  if (open) {
    document.getElementById("notif-panel").classList.add("hidden");
    if (typeof openTask === "function") openTask(open.dataset.wnTask);
    else window.location.href = "/work";
  }
});

/* In Work mode the bell shows work activity. Registered in the capture phase
   so it runs before app.js's handler, which would otherwise repaint the panel
   with personal reminders. */
if (isWorkMode()) {
  document.getElementById("notif-bell")?.addEventListener("click", async () => {
    const panel = document.getElementById("notif-panel");
    if (panel?.classList.contains("hidden")) return;   // it was just closed
    await renderWorkNotifications();
    try {
      await apiFetch("/api/work/notifications/read", { method: "POST" });
      document.getElementById("notif-count")?.classList.add("hidden");
      document.getElementById("ps-work-badge")?.classList.add("hidden");
    } catch (_) {}
  }, true);

  renderWorkNotifications();
  // Slow on purpose. The badge should be current without the page becoming a
  // polling client; anything a user does themselves refreshes it immediately.
  setInterval(renderWorkNotifications, 60_000);
  window.addEventListener("work-updated", renderWorkNotifications);
} else {
  // Personal mode still surfaces the count, so pending work is visible from
  // the other side of the switcher.
  (async () => {
    try {
      const d = await apiFetch("/api/work/notifications");
      const tab = document.getElementById("ps-work-badge");
      if (tab && d.unread) {
        tab.textContent = d.unread > 9 ? "9+" : d.unread;
        tab.classList.remove("hidden");
      }
    } catch (_) {}
  })();
}
