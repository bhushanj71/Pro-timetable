/* Work mode: communities, assignments, progress.

   Reads go through the same cache as the rest of the app, under work: keys so
   nothing here can be served from a personal-mode entry or vice versa. */

const wkModal = (id, open = true) =>
  document.getElementById(id)?.classList.toggle("hidden", !open);

document.querySelectorAll("[data-close-modal]").forEach((b) =>
  b.addEventListener("click", () => wkModal(b.dataset.closeModal, false))
);

const PRIORITY_DOT = { low: "\u{1F7E2}", medium: "\u{1F7E1}", high: "\u{1F7E0}", urgent: "\u{1F534}" };
const STATUS_LABEL = {
  pending: "Awaiting your answer",
  accepted: "Accepted",
  in_progress: "In progress",
  completed: "Completed",
  declined: "Declined",
};

/* How many rows a card shows before "View all". Three keeps every card on the
   dashboard the same height, so the page reads as a summary rather than as
   three lists of unpredictable length. */
const PREVIEW = 3;

/* Everything the dashboard drew last, so filtering and expanding redraw from
   memory instead of going back to the server on every keystroke. */
const WK = { data: null, created: [], query: "", expanded: {}, sort: "recent",
             community: null, pickable: [], profile: null, openTask: null,
             attachmentsOpen: false };

/* Which page this is. work.js runs on all three: the dashboard, a task page
   and a community page. The dataset attributes come from work_detail.html. */
const DETAIL = document.querySelector(".wk-detail-page");
const DETAIL_KIND = DETAIL?.dataset.detailKind || null;
const DETAIL_ID = DETAIL?.dataset.detailId || null;

/* Redraws whatever this page is showing, whichever page that is. Handlers
   call this rather than loadWork() so one code path serves all three. */
function refreshView() {
  if (DETAIL_KIND === "task") return openTask(DETAIL_ID);
  if (DETAIL_KIND === "community") return openCommunity(DETAIL_ID);
  return loadWork();
}

/* The dashboard counts are stale after most actions -- but only if there is a
   dashboard. On a detail page there is nothing behind this to refresh, and
   fetching three endpoints to redraw a page that is not on screen is just a
   slower way to do nothing. */
const syncDashboard = () => (DETAIL ? Promise.resolve() : loadWork());

function bar(pct, cls = "") {
  return `<div class="wk-bar ${cls}"><span style="width:${Math.max(0, Math.min(100, pct))}%"></span></div>`;
}

/* Name over department, everywhere a person appears. Two colleagues can
   share a name; only one of them is the one being assigned to. */
function personLines(p) {
  return `
    <div class="wk-id">
      <div class="wk-person-name">${esc(p.name)}</div>
      ${p.department ? `<div class="wk-id-dept">🏢 ${esc(p.department)}</div>` : ""}
    </div>`;
}

function personRow(a, canManage = false, taskId = null) {
  const pending = a.status === "pending";
  const declined = a.status === "declined";
  return `
    <div class="wk-person">
      <span class="wk-avatar">${esc(a.user.initial)}</span>
      ${personLines(a.user)}
      ${pending ? `<span class="wk-chip pending">⏳ Not answered yet</span>`
        : declined ? `<span class="wk-chip declined">✕ Declined${a.decline_reason ? " · " + esc(a.decline_reason) : ""}</span>`
        : `<span class="wk-person-progress"><span class="wk-person-bar">${bar(a.progress)}</span><span class="wk-pct">${a.progress}%</span></span>`}
      ${canManage ? `
        <span class="wk-person-actions">
          <button class="chip-x" data-reassign="${esc(a.user.id)}" data-task="${esc(taskId)}"
                  data-name="${esc(a.user.name)}" title="Hand to someone else"
                  aria-label="Reassign ${esc(a.user.name)}">⇄</button>
          <button class="chip-x" data-unassign="${esc(a.user.id)}" data-task="${esc(taskId)}"
                  data-name="${esc(a.user.name)}" title="Take off this task"
                  aria-label="Remove ${esc(a.user.name)}">✕</button>
        </span>` : ""}
    </div>`;
}

/* ---------------- Sparklines ----------------
   Drawn from the seven daily readings the server sends, never invented. A
   series that never moved is drawn as a dashed flat line rather than a solid
   one, because a confident horizontal stroke reads as a measurement and this
   is the absence of one. */
function sparkline(series, tone) {
  if (!Array.isArray(series) || series.length < 2) return "";
  const W = 76, H = 26, PAD = 3;
  const lo = Math.min(...series), hi = Math.max(...series);
  const flat = hi === lo;
  const span = flat ? 1 : hi - lo;
  const x = (i) => PAD + (i * (W - PAD * 2)) / (series.length - 1);
  const y = (v) => (flat ? H / 2 : H - PAD - ((v - lo) / span) * (H - PAD * 2));

  const pts = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");

  if (flat) {
    return `<svg viewBox="0 0 ${W} ${H}" class="wk-spark-svg" aria-hidden="true">
      <polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="2"
                stroke-linecap="round" stroke-dasharray="5 5" opacity="0.45"/></svg>`;
  }
  const id = `sg-${tone}`;
  return `<svg viewBox="0 0 ${W} ${H}" class="wk-spark-svg" aria-hidden="true">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="currentColor" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="currentColor" stop-opacity="0"/>
    </linearGradient></defs>
    <polygon points="${pts} ${x(series.length - 1).toFixed(1)},${H} ${x(0).toFixed(1)},${H}" fill="url(#${id})"/>
    <polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="${x(series.length - 1).toFixed(1)}" cy="${y(series[series.length - 1]).toFixed(1)}" r="2.4" fill="currentColor"/>
  </svg>`;
}

/* The caption under each count, said from the data -- so it can report a real
   problem instead of always cheering. */
function statNote(kind, data) {
  const field = kind === "done" ? "completed" : kind;
  const n = data.counts[field];
  const series = (data.trends || {})[field] || [];
  const week = series.length ? n - series[0] : 0;

  if (kind === "active") {
    if (!n) return "Nothing on your plate";
    const now = new Date();
    const overdue = data.active_tasks.filter((t) => t.due_date && new Date(t.due_date) < now).length;
    if (overdue) return `${overdue} past its due date`;
    if (data.due_soon.length) return `${data.due_soon.length} due within 2 days`;
    return "Keep going — you're on track";
  }
  if (kind === "pending") {
    return n ? `${n} waiting on your answer` : "No pending tasks";
  }
  if (!n) return "Nothing finished yet";
  return week > 0 ? `+${week} finished this week` : "Good work";
}

function paintStat(kind, id, data) {
  const field = kind === "done" ? "completed" : kind;
  const el = document.getElementById(`wk-n-${id}`);
  if (el) el.textContent = data.counts[field];
  const spark = document.getElementById(`wk-spark-${id}`);
  if (spark) spark.innerHTML = sparkline((data.trends || {})[field], id);
  const note = document.getElementById(`wk-note-${id}`);
  if (note) note.textContent = statNote(kind, data);
}

/* ---------------- Rows ---------------- */
const matches = (text) => !WK.query || String(text).toLowerCase().includes(WK.query);

function taskRow(t, pct, meta, badge = "") {
  return `
    <a class="wk-task" href="/work/task/${encodeURIComponent(t.id)}">
      <div class="wk-task-row">
        <span class="wk-dot" data-p="${esc(t.priority || "medium")}"></span>
        <div class="wk-task-main">
          <div class="wk-task-title">${esc(t.title)}</div>
          <div class="wk-task-meta">${meta}</div>
        </div>
        <div class="wk-task-right">
          <span class="wk-pct">${pct}%</span>
          ${badge}
        </div>
      </div>
      ${bar(pct)}
    </a>`;
}

/* Renders at most PREVIEW rows and wires the card's own "View all" toggle. */
function paintList(boxId, buttonId, key, items, render, emptyText, label) {
  const box = document.getElementById(boxId);
  const btn = document.getElementById(buttonId);
  if (!box) return;

  if (!items.length) {
    box.innerHTML = `<div class="wk-empty">${esc(emptyText)}</div>`;
    if (btn) btn.classList.add("hidden");
    return;
  }

  const open = WK.expanded[key];
  box.innerHTML = (open ? items : items.slice(0, PREVIEW)).map(render).join("");

  if (!btn) return;
  if (items.length <= PREVIEW) {
    btn.classList.add("hidden");
    return;
  }
  btn.classList.remove("hidden");
  btn.querySelector("span").textContent = open ? "Show less" : `View all ${items.length} ${label}`;
  btn.classList.toggle("is-open", !!open);
}

/* ---------------- Dashboard ---------------- */
function paintDashboard() {
  const data = WK.data;
  if (!data) return;

  paintStat("active", "active", data);
  paintStat("pending", "pending", data);
  paintStat("done", "done", data);

  // --- Community invitations ---
  const invitesBox = document.getElementById("wk-invites");
  if (invitesBox) invitesBox.innerHTML = data.invitations.map((i) => `
    <div class="wk-request">
      <div class="wk-request-main">
        <div class="wk-request-title">${esc(i.community.icon)} ${esc(i.community.name)}</div>
        <div class="wk-request-meta">${esc(i.from.name)} invited you${i.message ? " · " + esc(i.message) : ""}</div>
      </div>
      <div class="wk-request-actions">
        <button class="btn btn-sm btn-primary" data-invite="${i.id}" data-accept="1">Join</button>
        <button class="btn btn-sm" data-invite="${i.id}" data-accept="0">Decline</button>
      </div>
    </div>`).join("");

  // --- Task requests ---
  const requestsBox = document.getElementById("wk-requests");
  if (requestsBox) requestsBox.innerHTML = data.requests.map((t) => `
    <div class="wk-request">
      <div class="wk-request-main">
        <div class="wk-request-title"><span class="wk-dot" data-p="${esc(t.priority || "medium")}"></span> ${esc(t.title)}</div>
        <div class="wk-request-meta">
          ${esc(t.creator.name)} · ${esc(t.community.name)}${t.due_date ? " · due " + fmtDate(t.due_date) : ""}
        </div>
      </div>
      <div class="wk-request-actions">
        <button class="btn btn-sm btn-primary" data-task-respond="${t.id}" data-accept="1">✓ Accept</button>
        <button class="btn btn-sm" data-task-respond="${t.id}" data-accept="0">✕ Decline</button>
      </div>
    </div>`).join("");

  // The illustration is the empty state. Once something is genuinely waiting,
  // it gets out of the way rather than decorating an obligation.
  const waiting = data.invitations.length + data.requests.length;
  document.getElementById("wk-inbox-card")?.classList.toggle("has-items", waiting > 0);
  document.getElementById("wk-inbox-art")?.classList.toggle("hidden", waiting > 0);
  const sub = document.getElementById("wk-inbox-sub");
  if (sub) {
    sub.textContent = waiting
      ? `${waiting} ${waiting === 1 ? "thing needs" : "things need"} your answer.`
      : "Nothing needs your answer right now.";
  }

  // --- My active tasks ---
  const active = data.active_tasks.filter((t) => matches(t.title) || matches(t.community.name));
  paintList("wk-active", "wk-viewall-active", "active", active,
    (t) => taskRow(t, t.my_progress,
      `${esc(t.community.name)}${t.due_date ? " · due " + fmtDate(t.due_date) : ""}`),
    WK.query ? "No active tasks match that search." : "No active tasks. Accepted work appears here.",
    "active tasks");

  // --- Communities ---
  paintList("wk-communities", "wk-viewall-communities", "communities",
    data.communities.filter((c) => matches(c.name)),
    (c) => `
      <a class="wk-community" href="/work/community/${encodeURIComponent(c.id)}">
        <span class="wk-community-icon">${esc(c.icon)}</span>
        <div class="wk-community-main">
          <div class="wk-community-name">${esc(c.name)}</div>
          <div class="wk-task-meta">${c.member_count} member${c.member_count === 1 ? "" : "s"} · ${esc(c.my_role)}</div>
        </div>
        <span class="wk-chev" aria-hidden="true">›</span>
      </a>`,
    WK.query ? "No communities match that search." : "No communities yet. Create one to start assigning work.",
    "communities");

  paintCreated();
}

const SORTERS = {
  recent: () => 0,   // the server already returns newest first
  progress: (a, b) => b.progress.overall - a.progress.overall,
  title: (a, b) => a.title.localeCompare(b.title),
  due: (a, b) => {
    if (!a.due_date) return 1;
    if (!b.due_date) return -1;
    return new Date(a.due_date) - new Date(b.due_date);
  },
};

function paintCreated() {
  const items = WK.created
    .filter((t) => matches(t.title) || matches(t.community.name))
    .slice()
    .sort(SORTERS[WK.sort] || SORTERS.recent);

  paintList("wk-created", "wk-viewall-created", "created", items, (t) => {
    const p = t.progress;
    const done = p.overall === 100 && p.accepted > 0;
    const meta = `${p.accepted} accepted${p.pending ? ` · ${p.pending} pending` : ""}` +
      `${p.declined ? ` · ${p.declined} declined` : ""} · ${esc(t.community.name)}`;
    return taskRow(t, p.overall, meta, done ? `<span class="wk-badge done">Completed</span>` : "");
  }, WK.query ? "Nothing assigned matches that search." : "Nothing assigned yet.", "assigned tasks");
}

async function loadWork() {
  const [data, invites, created] = await Promise.all([
    cachedFetch("/api/work/dashboard"),
    cachedFetch("/api/work/invitations"),
    cachedFetch("/api/work/tasks?scope=created"),
  ]);
  WK.data = { ...data, invitations: invites.invitations };
  WK.created = created.tasks;
  paintDashboard();
}

/* ---------------- Search, expand, sort ---------------- */
let wkSearchTimer;
document.getElementById("wk-search")?.addEventListener("input", (e) => {
  const value = e.target.value.trim().toLowerCase();
  document.getElementById("wk-search-clear")?.classList.toggle("hidden", !value);
  clearTimeout(wkSearchTimer);
  // Short debounce: filtering is local, but redrawing three lists on every
  // keypress of a fast typist is wasted work.
  wkSearchTimer = setTimeout(() => {
    WK.query = value;
    paintDashboard();
  }, 90);
});

document.getElementById("wk-search-clear")?.addEventListener("click", () => {
  const input = document.getElementById("wk-search");
  input.value = "";
  WK.query = "";
  document.getElementById("wk-search-clear").classList.add("hidden");
  paintDashboard();
  input.focus();
});

const dirQuery = () => document.getElementById("wk-dir-q")?.value.trim() || "";
const dirDept = () => document.getElementById("wk-dir-filter")?.value || "";

document.addEventListener("input", (e) => {
  if (e.target.id !== "wk-dir-q") return;
  clearTimeout(dirTimer);
  dirTimer = setTimeout(() => loadDirectory(WK.community, dirQuery(), dirDept()), 220);
});

document.addEventListener("change", (e) => {
  if (e.target.id === "wk-dir-filter") loadDirectory(WK.community, dirQuery(), dirDept());
});

document.getElementById("wk-created-sort")?.addEventListener("change", (e) => {
  WK.sort = e.target.value;
  paintCreated();
});

document.querySelectorAll(".wk-viewall").forEach((btn) => {
  const key = btn.id.replace("wk-viewall-", "");
  btn.addEventListener("click", () => {
    WK.expanded[key] = !WK.expanded[key];
    paintDashboard();
  });
});

/* A detail page whose data will not load. As a dialog this could just be
   closed; a page has to say what happened and offer the way out, or it is a
   dead end with a spinner on it. */
function detailError(body, err, what) {
  const status = err?.status;
  const message = status === 404 ? `This ${what} no longer exists.`
    : status === 403 ? `You do not have access to this ${what}.`
    : err?.message || "Something went wrong loading this.";
  body.innerHTML = `
    <div class="wk-detail-error">
      <div class="wk-detail-error-art" aria-hidden="true">🔍</div>
      <p>${esc(message)}</p>
      <a class="btn btn-primary" href="/work">Back to Work</a>
    </div>`;
}

/* Back, and back means back: the history entry if there is one, so the
   dashboard returns scrolled and filtered as it was left. Arriving cold --
   from a notification, or a pasted link -- there is no such entry, and going
   "back" would leave the app entirely. */
document.getElementById("wk-back")?.addEventListener("click", () => {
  const cameFromApp = document.referrer &&
    new URL(document.referrer, location.href).host === location.host;
  if (window.history.length > 1 && cameFromApp) window.history.back();
  else window.location.assign("/work");
});

/* ---------------- Task detail ---------------- */
async function openTask(taskId) {
  const body = document.getElementById("wk-taskdetail-body");
  if (!body) return;
  body.innerHTML = `<div class="sk sk-line long"></div><div class="sk sk-line medium"></div>`;

  let t;
  try {
    t = await cachedFetch(`/api/work/tasks/${taskId}`, { force: true });
  } catch (err) {
    return detailError(body, err, "task");
  }
  // Kept so the manage controls can read the roster without refetching.
  WK.openTask = t;
  // The server marks the caller's own row; matching on display name here
  // was broken and would have confused two members sharing a name.
  const me = t.assignments.find((a) => a.is_me);
  const p = t.progress;

  body.innerHTML = `
    <h3 style="margin:0 0 4px">${PRIORITY_DOT[t.priority] || ""} ${esc(t.title)}</h3>
    <p class="muted-text" style="margin:0 0 12px">
      ${esc(t.community.name)} · assigned by ${esc(t.creator.name)}${t.due_date ? " · due " + fmtDate(t.due_date) : ""}
    </p>
    ${t.description ? `<p style="margin:0 0 14px">${esc(t.description)}</p>` : ""}

    <div class="wk-overall">
      <div class="wk-task-head"><strong>Overall ${p.overall}%</strong></div>
      ${bar(p.overall)}
      <div class="wk-basis">${esc(p.basis)}</div>
    </div>

    ${attachmentBlock(t)}

    <div class="wk-people">${t.assignments.map((a) => personRow(a, t.can_manage, t.id)).join("")}</div>

    ${t.can_manage ? `
      <div class="wk-manage">
        <button class="wk-manage-toggle" id="wk-manage-toggle" aria-expanded="false"
                aria-controls="wk-manage-panel">
          <span>⚙️ Manage task</span><span class="wk-chev" aria-hidden="true">›</span>
        </button>

        <div class="wk-manage-panel hidden" id="wk-manage-panel">
          <div class="form-group">
            <label for="wk-add-member">Add someone</label>
            <div class="reminder-add">
              <select id="wk-add-member" data-task="${t.id}"></select>
              <button class="btn btn-sm btn-primary" id="wk-add-assignee" data-task="${t.id}">+ Add</button>
            </div>
            <small class="form-hint">They are asked to accept, exactly as at assignment.</small>
          </div>

          <div class="wk-manage-sep"></div>

          <div class="form-row">
            <div class="form-group">
              <label for="wk-edit-title">Title</label>
              <input type="text" id="wk-edit-title" maxlength="255" value="${esc(t.title)}">
            </div>
            <div class="form-group" style="flex:0 0 140px">
              <label for="wk-edit-priority">Priority</label>
              <select id="wk-edit-priority">
                ${["low", "medium", "high", "urgent"].map((p) =>
                  `<option value="${p}"${t.priority === p ? " selected" : ""}>${p[0].toUpperCase() + p.slice(1)}</option>`).join("")}
              </select>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label for="wk-edit-due">Due</label>
              <input type="date" id="wk-edit-due" value="${t.due_date ? t.due_date.slice(0, 10) : ""}">
            </div>
            <div class="form-group">
              <label for="wk-edit-desc">Details</label>
              <input type="text" id="wk-edit-desc" maxlength="4000" value="${esc(t.description || "")}">
            </div>
          </div>
          <button class="btn btn-sm btn-primary" id="wk-update-task" data-task="${t.id}">Update task</button>
          <small class="form-hint">Everyone still carrying this task is told what changed.</small>
        </div>
      </div>` : ""}

    ${me && ["accepted", "in_progress", "completed"].includes(me.status) ? `
      <div class="form-group wk-progress-editor" style="margin-top:16px">
        <label for="wk-my-progress">
          My progress — <strong id="wk-my-progress-value">${me.progress}%</strong>
          <span class="wk-dirty hidden" id="wk-progress-dirty">unsaved</span>
        </label>
        <div class="aslider" id="wk-my-progress-slider" style="--pct:${me.progress}">
          <div class="aslider-track">
            <i></i><i></i><i></i><i></i><i></i><i></i>
            <div class="aslider-fill"></div>
            <div class="aslider-thumb"></div>
          </div>
          <input type="range" id="wk-my-progress" min="0" max="100" step="5" value="${me.progress}"
                 data-initial="${me.progress}" aria-describedby="wk-my-progress-value">
        </div>
        <input type="text" id="wk-my-note" placeholder="What changed? (optional)" maxlength="2000" style="margin-top:8px">
        <button class="btn btn-sm btn-primary" id="wk-save-progress" data-task="${t.id}" style="margin-top:8px">Save progress</button>
      </div>` : ""}

    ${me && me.status === "pending" ? `
      <div class="modal-actions" style="margin-top:8px">
        <button class="btn btn-primary" data-task-respond="${t.id}" data-accept="1">✓ Accept task</button>
        <button class="btn" data-task-respond="${t.id}" data-accept="0">✕ Decline</button>
      </div>` : ""}

    ${t.timeline?.length ? `
      <div class="wk-timeline">
        <strong>History</strong>
        ${t.timeline.slice(0, 8).map((u) => `
          <div class="wk-tl-row">
            <span class="wk-tl-when">${fmtDate(u.at)}</span>
            <span>${esc(u.user.name)}${u.to != null ? ` — ${u.from ?? 0}% → ${u.to}%` : ""}${u.note ? ` · ${esc(u.note)}` : ""}</span>
          </div>`).join("")}
      </div>` : ""}`;

  // Start in the right colour stage: a task already at 100% should open green
  // rather than amber until somebody happens to touch the slider.
  const slider = document.getElementById("wk-my-progress-slider");
  if (slider && me) paintSlider(slider, me.progress);
}

/* ---------------- Attachments ----------------

   Rendered directly under the progress bar, which is where the evidence for a
   number belongs: "80%" and the report that justifies it should be read in one
   glance, not one above the fold and the other below it.

   Long lists collapse. A task with fifteen files would otherwise push the
   roster, the history and every control off the screen, and the common case is
   two or three. */
const ATTACHMENTS_SHOWN = 3;

function attachmentRow(a, canRemove) {
  const view = a.can_view_inline
    ? `<a class="wk-att-act lnk lnk-arrow" href="/api/work/attachments/${esc(a.id)}"
           target="_blank" rel="noopener">View</a>`
    : "";
  return `
    <div class="wk-att" data-attachment="${esc(a.id)}">
      <span class="wk-att-icon" aria-hidden="true">${esc(a.icon)}</span>
      <div class="wk-att-main">
        <div class="wk-att-name">${esc(a.file_name)}</div>
        <div class="wk-att-meta">Uploaded by ${esc(a.uploaded_by.name)} · ${esc(a.size)}</div>
      </div>
      <div class="wk-att-actions">
        ${view}
        <a class="wk-att-act lnk lnk-wipe" href="/api/work/attachments/${esc(a.id)}?download=true">Download</a>
        ${canRemove ? `<button class="chip-x" data-remove-attachment="${esc(a.id)}"
                 aria-label="Remove ${esc(a.file_name)}">✕</button>` : ""}
      </div>
    </div>`;
}

function attachmentBlock(t) {
  const files = t.attachments || [];
  const expanded = WK.attachmentsOpen;
  const shown = expanded ? files : files.slice(0, ATTACHMENTS_SHOWN);
  const hidden = files.length - shown.length;

  return `
    <div class="wk-atts" id="wk-atts">
      <div class="wk-atts-head">
        <strong>Attachments</strong>
        ${files.length ? `<span class="wk-atts-count">${files.length}</span>` : ""}
      </div>
      ${files.length
        ? shown.map((a) => attachmentRow(a, a.can_remove !== false)).join("")
        : `<p class="wk-atts-empty">No files yet. Attach the work, or anything needed to do it.</p>`}
      ${hidden > 0 ? `<button class="wk-atts-more" id="wk-atts-more">View all ${files.length} attachments</button>` : ""}
      ${expanded && files.length > ATTACHMENTS_SHOWN ? `<button class="wk-atts-more" id="wk-atts-less">Show fewer</button>` : ""}

      ${t.can_attach !== false ? `
        <label class="wk-att-add" for="wk-att-input">
          <span aria-hidden="true">+</span> Attach file
          <input type="file" id="wk-att-input" data-task="${esc(t.id)}" hidden
                 accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.jpg,.jpeg,.png,.zip,.txt">
        </label>
        <div class="wk-att-progress hidden" id="wk-att-progress">
          <div class="wk-bar"><span id="wk-att-bar" style="width:0%"></span></div>
          <span class="wk-att-progress-label" id="wk-att-label">Uploading…</span>
        </div>` : ""}
    </div>`;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#wk-atts-more")) {
    WK.attachmentsOpen = true;
    if (WK.openTask) openTask(WK.openTask.id);
  }
  if (e.target.closest("#wk-atts-less")) {
    WK.attachmentsOpen = false;
    if (WK.openTask) openTask(WK.openTask.id);
  }
});

/* XMLHttpRequest rather than fetch: it reports upload progress, and fetch
   still cannot. A 4 MB file over a phone connection is long enough that a
   silent wait reads as a failure. */
document.addEventListener("change", (e) => {
  const input = e.target.closest("#wk-att-input");
  if (!input || !input.files?.length) return;

  const file = input.files[0];
  const taskId = input.dataset.task;
  const wrap = document.getElementById("wk-att-progress");
  const bar_ = document.getElementById("wk-att-bar");
  const label = document.getElementById("wk-att-label");
  wrap?.classList.remove("hidden");

  const form = new FormData();
  form.append("file", file);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/work/tasks/${taskId}/attachments`);
  xhr.withCredentials = true;
  xhr.upload.addEventListener("progress", (ev) => {
    if (!ev.lengthComputable) return;
    const pct = Math.round((ev.loaded / ev.total) * 100);
    if (bar_) bar_.style.width = `${pct}%`;
    if (label) label.textContent = pct < 100 ? `Uploading ${pct}%` : "Saving…";
  });
  xhr.addEventListener("load", async () => {
    wrap?.classList.add("hidden");
    input.value = "";
    if (xhr.status === 201) {
      showToast(`${file.name} attached`, "success");
      await openTask(taskId);
    } else {
      let message = "Could not attach that file";
      try { message = JSON.parse(xhr.responseText).detail || message; } catch (_) {}
      showToast(message, "error");
    }
  });
  xhr.addEventListener("error", () => {
    wrap?.classList.add("hidden");
    input.value = "";
    showToast("Could not attach that file", "error");
  });
  xhr.send(form);
});

document.addEventListener("click", async (e) => {
  const remove = e.target.closest("[data-remove-attachment]");
  if (!remove) return;
  if (!confirm("Remove this file? This cannot be undone.")) return;
  try {
    await apiFetch(`/api/work/attachments/${remove.dataset.removeAttachment}`, { method: "DELETE" });
    showToast("File removed", "success");
    if (WK.openTask) await openTask(WK.openTask.id);
  } catch (err) { showToast(err.message, "error"); }
});

/* ---------------- Community detail ---------------- */
async function openCommunity(id) {
  const body = document.getElementById("wk-detail-body");
  if (!body) return;
  body.innerHTML = `<div class="sk sk-line long"></div><div class="sk sk-line medium"></div>`;

  let c;
  try {
    c = await cachedFetch(`/api/work/communities/${id}`, { force: true });
  } catch (err) {
    return detailError(body, err, "community");
  }
  const canInvite = c.my_role === "owner" || c.my_role === "admin";
  const isOwner = c.my_role === "owner";
  WK.community = id;

  body.innerHTML = `
    <h3 style="margin:0 0 4px">${esc(c.icon)} ${esc(c.name)}</h3>
    <p class="muted-text" style="margin:0 0 14px">${esc(c.description || "No description")}</p>

    <div class="btn-row" style="margin-bottom:14px">
      <button class="btn btn-sm btn-primary" data-assign-in="${c.id}">+ Assign a task</button>
      <a class="btn btn-sm" href="/work/community/${encodeURIComponent(c.id)}/board">📊 Work board</a>
      ${canInvite ? `<button class="btn btn-sm" data-invite-to="${c.id}">+ Invite by email</button>` : ""}
      ${canInvite ? `<button class="btn btn-sm" data-directory-for="${c.id}">\u{1F3EB} Add from my college</button>` : ""}
    </div>

    ${canInvite ? `
      <div class="form-group hidden" id="wk-invite-row">
        <label for="wk-invite-email">Invite by email</label>
        <div class="reminder-add">
          <input type="email" id="wk-invite-email" placeholder="colleague@college.edu">
          <button class="btn btn-sm btn-primary" id="wk-invite-send" data-community="${c.id}">Send</button>
        </div>
        <small class="form-hint">They receive an invitation and choose whether to join.</small>
      </div>` : ""}

    ${canInvite ? `
      <div class="wk-directory hidden" id="wk-dir-row">
        <div class="wk-filter-row">
          <div class="wk-search wk-search-inline">
            <span class="wk-search-icon" aria-hidden="true">\u{1F50D}</span>
            <input type="search" id="wk-dir-q" placeholder="Search by name, department or role"
                   autocomplete="off" aria-label="Search colleagues at your college">
          </div>
          <select id="wk-dir-filter" aria-label="Filter by department">
            <option value="">All departments</option>
          </select>
        </div>
        <div id="wk-dir-results"><div class="wk-empty">Loading colleagues…</div></div>
      </div>` : ""}

    <div class="wk-people">
      ${c.members.map((m) => `
        <div class="wk-person">
          <span class="wk-avatar">${esc(m.initial)}</span>
          ${personLines(m)}
          <span class="wk-chip role">${esc(m.role)}</span>
          ${canInvite && m.role !== "owner" ? `<button class="chip-x" data-remove-member="${m.id}" data-community="${c.id}" aria-label="Remove">✕</button>` : ""}
        </div>`).join("")}
      ${c.pending_invites.map((p) => `
        <div class="wk-person">
          <span class="wk-avatar">${esc(p.initial)}</span>
          ${personLines(p)}
          <span class="wk-chip pending">⏳ Invited</span>
        </div>`).join("")}
    </div>

    ${isOwner ? `
      <div class="wk-danger-zone">
        <div class="wk-danger-text">
          <strong>Delete this community</strong>
          <span>Removes every task, assignment and progress history in it, for everyone.</span>
        </div>
        <button class="btn btn-sm btn-danger" data-delete-community="${c.id}">Delete</button>
      </div>` : ""}`;
}

/* ---------------- Managing a task ----------------
   Everything here is refused by the server too, for the creator and the
   community admins only. These controls exist so the people who may do it can
   see how, not to decide who may. */

/* Community members not already on the task, for the add and reassign
   pickers. Fetched fresh because the sheet may have been open a while. */
async function candidatesFor(taskId, community, exclude) {
  const c = await cachedFetch(`/api/work/communities/${community}`, { force: true });
  return c.members.filter((m) => !exclude.has(m.id));
}

async function fillAddPicker(task) {
  const select = document.getElementById("wk-add-member");
  if (!select) return;
  const taken = new Set(task.assignments.map((a) => a.user.id));
  const free = await candidatesFor(task.id, task.community.id, taken);

  select.innerHTML = free.length
    ? free.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}${m.department ? ` — ${esc(m.department)}` : ""}</option>`).join("")
    : `<option value="">Everyone in this community is already on it</option>`;
  select.disabled = !free.length;
  document.getElementById("wk-add-assignee").disabled = !free.length;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#wk-manage-toggle")) {
    const panel = document.getElementById("wk-manage-panel");
    const btn = document.getElementById("wk-manage-toggle");
    const opening = panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !opening);
    btn.setAttribute("aria-expanded", String(opening));
    btn.classList.toggle("is-open", opening);
    if (opening && WK.openTask) fillAddPicker(WK.openTask);
  }
});

document.addEventListener("click", async (e) => {
  // --- Add ---
  const add = e.target.closest("#wk-add-assignee");
  if (add) {
    const id = document.getElementById("wk-add-member").value;
    if (!id) return;
    setButtonLoading(add, true);
    try {
      const r = await apiFetch(`/api/work/tasks/${add.dataset.task}/assignees`,
        { method: "POST", body: { user_ids: [id] } });
      showToast(r.added ? "Added — they have been asked to accept" : "They were already on it", "success");
      await openTask(add.dataset.task);
      await syncDashboard();
    } catch (err) { showToast(err.message, "error"); }
    finally { setButtonLoading(add, false); }
    return;
  }

  // --- Remove ---
  const off = e.target.closest("[data-unassign]");
  if (off) {
    const { task, unassign, name } = off.dataset;
    let cost = null;
    try {
      cost = await apiFetch(`/api/work/tasks/${task}/assignees/${unassign}/removal-cost`);
    } catch (_) { /* the confirmation below still stands without it */ }

    // Asked with the cost in the question. "Are you sure?" is not informed
    // consent when it does not mention the 60% and four updates going with it.
    const lost = cost && cost.accepted
      ? `\n\n${cost.name} accepted this and is at ${cost.progress}%.` +
        (cost.updates ? ` ${cost.updates} progress update${cost.updates === 1 ? "" : "s"} will be lost.` : "")
      : "";
    if (!confirm(`Take ${name} off this task?${lost}`)) return;

    try {
      await apiFetch(`/api/work/tasks/${task}/assignees/${unassign}`, { method: "DELETE" });
      showToast(`${name} was taken off the task`, "success");
      await openTask(task);
      await syncDashboard();
    } catch (err) { showToast(err.message, "error"); }
    return;
  }

  // --- Reassign ---
  const swap = e.target.closest("[data-reassign]");
  if (swap) {
    const { task, reassign, name } = swap.dataset;
    const current = WK.openTask;
    if (!current) return;
    const taken = new Set(current.assignments.map((a) => a.user.id));
    let free = [];
    try {
      free = await candidatesFor(task, current.community.id, taken);
    } catch (err) { return showToast(err.message, "error"); }
    if (!free.length) return showToast("Everyone in this community is already on this task", "error");

    const choice = prompt(
      `Hand ${name}'s place to whom?\n\n` +
      free.map((m, i) => `${i + 1}. ${m.name}${m.department ? ` — ${m.department}` : ""}`).join("\n") +
      `\n\nType a number. ${name}'s progress is not carried over.`);
    const picked = free[Number(choice) - 1];
    if (!picked) return;

    try {
      await apiFetch(`/api/work/tasks/${task}/reassign`,
        { method: "POST", body: { from_user_id: reassign, to_user_id: picked.id } });
      showToast(`Passed to ${picked.name} — they have been asked to accept`, "success");
      await openTask(task);
      await syncDashboard();
    } catch (err) { showToast(err.message, "error"); }
    return;
  }

  // --- Update the task itself ---
  const save = e.target.closest("#wk-update-task");
  if (save) {
    const due = document.getElementById("wk-edit-due").value;
    setButtonLoading(save, true);
    try {
      const r = await apiFetch(`/api/work/tasks/${save.dataset.task}`, {
        method: "PUT",
        body: {
          title: document.getElementById("wk-edit-title").value.trim(),
          description: document.getElementById("wk-edit-desc").value.trim() || null,
          priority: document.getElementById("wk-edit-priority").value,
          due_date: due ? new Date(due + "T17:00").toISOString() : null,
        },
      });
      // Says what moved, not just "saved" -- the professor is about to be
      // asked by whoever gets the notification.
      showToast(r.changed.length ? `Updated: ${r.changed.join(", ")}` : "Nothing changed", "success");
      await openTask(save.dataset.task);
      await syncDashboard();
    } catch (err) { showToast(err.message, "error"); }
    finally { setButtonLoading(save, false); }
  }
});

/* ---------------- The same-college directory ---------------- */
let dirTimer;
async function loadDirectory(communityId, q = "", departmentId = "") {
  const box = document.getElementById("wk-dir-results");
  if (!box) return;
  try {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (departmentId) params.set("department_id", departmentId);
    const query = params.toString();
    const d = await apiFetch(
      `/api/work/communities/${communityId}/directory${query ? `?${query}` : ""}`);

    // Built once per community, then left alone: rebuilding it on every
    // keystroke would reset the filter the person just chose.
    const filter = document.getElementById("wk-dir-filter");
    if (filter && !filter.dataset.filled && d.departments?.length) {
      filter.innerHTML = `<option value="">All departments</option>` +
        d.departments.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
      filter.dataset.filled = "1";
    }

    if (d.reason) {
      box.innerHTML = `<div class="wk-empty">${esc(d.reason)}</div>`;
      return;
    }
    if (!d.people.length) {
      box.innerHTML = `<div class="wk-empty">${q || departmentId
        ? "Nobody at your college matches that."
        : "Nobody else from your college has an account yet."}</div>`;
      return;
    }

    box.innerHTML = `
      <div class="wk-dir-scope">Showing people at <strong>${esc(d.college)}</strong></div>
      ${d.people.map((p) => `
        <div class="wk-person wk-dir-person">
          <span class="wk-avatar">${esc(p.initial)}</span>
          <div class="wk-dir-main">
            <div class="wk-person-name">${esc(p.name)}</div>
            ${p.department ? `<div class="wk-dir-meta">🏢 ${esc(p.department)}</div>` : ""}
            ${p.designation ? `<div class="wk-dir-meta wk-dir-role">${esc(p.designation)}</div>` : ""}
          </div>
          ${p.member ? `<span class="wk-chip role">Already in</span>`
            : p.invited ? `<span class="wk-chip pending">⏳ Invited</span>`
            : `<button class="btn btn-sm btn-primary" data-dir-invite="${p.id}" data-community="${communityId}">Invite</button>`}
        </div>`).join("")}
      ${d.truncated ? `<div class="wk-empty">More than ${d.people.length} people match. Narrow the search to find someone.</div>` : ""}`;
  } catch (err) {
    box.innerHTML = `<div class="wk-empty">${esc(err.message)}</div>`;
  }
}

/* ---------------- Deleting a community ---------------- */
/* Held between the two steps. The ticket is proof that the impact preview was
   actually fetched, and the server refuses the delete without it. */
const WK_DELETE = { ticket: null, id: null, phrase: "" };

async function openDeleteCommunity(communityId) {
  try {
    const d = await apiFetch(`/api/work/communities/${communityId}/deletion-preview`);
    WK_DELETE.ticket = d.ticket;
    WK_DELETE.id = communityId;
    WK_DELETE.phrase = d.confirm_phrase;

    document.getElementById("wk-del-name").textContent = `${d.icon} ${d.name}`;
    document.getElementById("wk-del-phrase").textContent = d.name;

    const i = d.impact;
    const rows = [
      [i.members, "member", "members"],
      [i.tasks, "task", "tasks"],
      [i.assignments, "assignment", "assignments"],
      [i.progress_updates, "progress update", "progress updates"],
    ].filter(([n]) => n > 0);

    document.getElementById("wk-del-impact").innerHTML = rows.length
      ? rows.map(([n, one, many]) => `<li><strong>${n}</strong> ${n === 1 ? one : many}</li>`).join("")
      : `<li>Nothing else is in it yet.</li>`;

    // The number that should actually stop someone: work other people
    // accepted and are partway through.
    const warn = document.getElementById("wk-del-warn");
    warn.classList.toggle("hidden", !i.unfinished_accepted);
    if (i.unfinished_accepted) {
      warn.textContent = `${i.unfinished_accepted} ${i.unfinished_accepted === 1
        ? "task someone accepted is unfinished" : "tasks people accepted are unfinished"}. ` +
        `Their progress goes too.`;
    }

    const input = document.getElementById("wk-del-confirm");
    input.value = "";
    document.getElementById("wk-del-go").disabled = true;
    wkModal("wk-delete-modal", true);
    input.focus();
  } catch (err) {
    showToast(err.message, "error");
  }
}

document.getElementById("wk-del-confirm")?.addEventListener("input", (e) => {
  const ok = e.target.value.trim().toLowerCase() === (WK_DELETE.phrase || "").trim().toLowerCase();
  document.getElementById("wk-del-go").disabled = !ok;
});

document.getElementById("wk-del-go")?.addEventListener("click", async (e) => {
  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch(`/api/work/communities/${WK_DELETE.id}`, {
      method: "DELETE",
      body: { confirm: document.getElementById("wk-del-confirm").value, ticket: WK_DELETE.ticket },
    });
    wkModal("wk-delete-modal", false);
    showToast("Community deleted", "success");
    // Standing on the page for a community that no longer exists is a dead
    // end -- and reloading it would only 404. Leave, and replace the history
    // entry so back does not walk straight into it.
    if (DETAIL_KIND === "community" && DETAIL_ID === WK_DELETE.id) {
      window.location.replace("/work");
      return;
    }
    await refreshView();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});

/* ---------------- Actions ---------------- */
document.addEventListener("click", async (e) => {
  const t = e.target;

  const invite = t.closest("[data-invite]");
  if (invite) {
    setButtonLoading(invite, true);
    try {
      await apiFetch(`/api/work/invitations/${invite.dataset.invite}/respond`,
        { method: "POST", body: { accept: invite.dataset.accept === "1" } });
      showToast(invite.dataset.accept === "1" ? "Joined" : "Declined", "success");
      await refreshView();
    } catch (err) { showToast(err.message, "error"); setButtonLoading(invite, false); }
    return;
  }

  const respond = t.closest("[data-task-respond]");
  if (respond) {
    setButtonLoading(respond, true);
    const accept = respond.dataset.accept === "1";
    let reason = null;
    if (!accept) reason = prompt("Anything to tell them? (optional)") || null;
    try {
      await apiFetch(`/api/work/tasks/${respond.dataset.taskRespond}/respond`,
        { method: "POST", body: { accept, reason } });
      showToast(accept ? "Task accepted — it's now in your active list" : "Task declined", "success");
      // Stays on the task rather than closing it: the answer changes what the
      // page shows -- a progress slider appears -- and that is worth seeing.
      await refreshView();
    } catch (err) { showToast(err.message, "error"); setButtonLoading(respond, false); }
    return;
  }

  if (t.closest("[data-invite-to]")) {
    document.getElementById("wk-invite-row")?.classList.remove("hidden");
    document.getElementById("wk-dir-row")?.classList.add("hidden");
    return;
  }

  const dirFor = t.closest("[data-directory-for]");
  if (dirFor) {
    document.getElementById("wk-invite-row")?.classList.add("hidden");
    const row = document.getElementById("wk-dir-row");
    row?.classList.remove("hidden");
    loadDirectory(dirFor.dataset.directoryFor);
    document.getElementById("wk-dir-q")?.focus();
    return;
  }

  const dirInvite = t.closest("[data-dir-invite]");
  if (dirInvite) {
    setButtonLoading(dirInvite, true);
    try {
      const r = await apiFetch(`/api/work/communities/${dirInvite.dataset.community}/invite`,
        { method: "POST", body: { user_id: dirInvite.dataset.dirInvite } });
      showToast(`Invitation sent to ${r.invited.name}`, "success");
      // Only this row changes, so the list is left where it is rather than
      // rebuilt underneath someone inviting several people in a row.
      dirInvite.outerHTML = `<span class="wk-chip pending">⏳ Invited</span>`;
    } catch (err) {
      showToast(err.message, "error");
      setButtonLoading(dirInvite, false);
    }
    return;
  }

  const delC = t.closest("[data-delete-community]");
  if (delC) return openDeleteCommunity(delC.dataset.deleteCommunity);

  if (t.id === "wk-invite-send") {
    const email = document.getElementById("wk-invite-email").value.trim();
    if (!email) return showToast("Enter an email address", "error");
    setButtonLoading(t, true);
    try {
      const r = await apiFetch(`/api/work/communities/${t.dataset.community}/invite`,
        { method: "POST", body: { email } });
      showToast(`Invitation sent to ${r.invited.name}`, "success");
      openCommunity(t.dataset.community);
    } catch (err) { showToast(err.message, "error"); }
    finally { setButtonLoading(t, false); }
    return;
  }

  const removeM = t.closest("[data-remove-member]");
  if (removeM) {
    if (!confirm("Remove this person from the community?")) return;
    try {
      await apiFetch(`/api/work/communities/${removeM.dataset.community}/members/${removeM.dataset.removeMember}`,
        { method: "DELETE" });
      showToast("Removed", "success");
      openCommunity(removeM.dataset.community);
      await syncDashboard();
    } catch (err) { showToast(err.message, "error"); }
    return;
  }

  const assignIn = t.closest("[data-assign-in]");
  if (assignIn) return openAssign(assignIn.dataset.assignIn);

  if (t.id === "wk-save-progress") {
    const progress = Number(document.getElementById("wk-my-progress").value);
    const note = document.getElementById("wk-my-note").value.trim() || null;
    setButtonLoading(t, true);
    try {
      await apiFetch(`/api/work/tasks/${t.dataset.task}/progress`, { method: "PUT", body: { progress, note } });
      showToast(`Progress saved — ${progress}%`, "success");
      await openTask(t.dataset.task);
      await syncDashboard();
    } catch (err) { showToast(err.message, "error"); }
    finally { setButtonLoading(t, false); }
  }
});

/* ---------------- Create community ---------------- */
document.getElementById("wk-new-community")?.addEventListener("click", () => wkModal("wk-community-modal", true));

document.getElementById("wk-c-create")?.addEventListener("click", async (e) => {
  const name = document.getElementById("wk-c-name").value.trim();
  if (!name) return showToast("Give it a name", "error");
  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch("/api/work/communities", { method: "POST", body: {
      name,
      description: document.getElementById("wk-c-desc").value.trim() || null,
      icon: document.getElementById("wk-c-icon").value.trim() || "👥",
    }});
    wkModal("wk-community-modal", false);
    document.getElementById("wk-c-name").value = "";
    showToast("Community created", "success");
    await loadWork();
  } catch (err) { showToast(err.message, "error"); }
  finally { setButtonLoading(e.currentTarget, false); }
});

/* ---------------- Assign a task ---------------- */
async function openAssign(communityId) {
  const c = await cachedFetch(`/api/work/communities/${communityId}`, { force: true });
  document.getElementById("wk-task-community").value = communityId;
  document.getElementById("wk-task-sub").textContent = `In ${c.name}`;
  // Kept so filtering redraws from memory and never loses a tick already made.
  WK.pickable = c.members;

  // The filter offers the departments actually present in this community,
  // not every department in the college -- an option that can only ever
  // return nothing is not a filter, it is a dead end.
  const filter = document.getElementById("wk-t-filter");
  if (filter) {
    const seen = new Map();
    for (const m of c.members) {
      if (m.department_id && !seen.has(m.department_id)) seen.set(m.department_id, m.department);
    }
    filter.innerHTML = `<option value="">All departments</option>` +
      [...seen].sort((a, b) => a[1].localeCompare(b[1]))
        .map(([id, name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join("");
  }
  const search = document.getElementById("wk-t-search");
  if (search) search.value = "";
  paintPicker();
  wkModal("wk-detail-modal", false);
  wkModal("wk-task-modal", true);
}

/* The assign picker: who you are assigning to, said in full.
   A list of first names is not enough to assign work by -- the department is
   what distinguishes two colleagues called Rahul. */
function paintPicker() {
  const box = document.getElementById("wk-t-members");
  if (!box) return;
  const picked = new Set(
    [...box.querySelectorAll("input:checked")].map((i) => i.value));

  const q = (document.getElementById("wk-t-search")?.value || "").trim().toLowerCase();
  const dept = document.getElementById("wk-t-filter")?.value || "";
  const people = (WK.pickable || []).filter((m) =>
    (!dept || m.department_id === dept) &&
    (!q || m.name.toLowerCase().includes(q) || (m.department || "").toLowerCase().includes(q)));

  box.innerHTML = people.length
    ? people.map((m) => `
        <label class="wk-pick">
          <input type="checkbox" value="${m.id}"${picked.has(m.id) ? " checked" : ""}>
          <span class="wk-avatar">${esc(m.initial)}</span>
          ${personLines(m)}
        </label>`).join("")
    : `<div class="wk-empty">Nobody in this community matches that.</div>`;

  // Anyone ticked and then filtered out is still being assigned, so they are
  // carried as hidden inputs rather than silently dropped.
  const shown = new Set(people.map((m) => m.id));
  for (const id of picked) {
    if (!shown.has(id)) {
      box.insertAdjacentHTML("beforeend",
        `<input type="checkbox" value="${esc(id)}" checked hidden>`);
    }
  }
}

document.addEventListener("input", (e) => {
  if (e.target.id === "wk-t-search") paintPicker();
});
document.addEventListener("change", (e) => {
  if (e.target.id === "wk-t-filter") paintPicker();
});

/* ---------------- The mandatory work profile ---------------- */
async function ensureWorkProfile() {
  let p;
  try {
    p = await apiFetch("/api/org/profile");
  } catch {
    return true;   // never lock someone out because a lookup failed
  }
  WK.profile = p;
  if (p.complete) return true;

  const college = document.getElementById("wk-p-college");
  const dept = document.getElementById("wk-p-department");
  const save = document.getElementById("wk-p-save");
  if (!college) return true;

  // With more than one college, a select left on its first option is a silent
  // guess -- the browser picks alphabetically, not correctly. A placeholder
  // makes the choice explicit; a single college is preselected because there
  // is nothing to choose between.
  const single = p.colleges.length === 1;
  college.innerHTML =
    (single ? "" : `<option value="">Select your college…</option>`) +
    p.colleges.map((c) =>
      `<option value="${esc(c.id)}">${esc(c.name)}${c.location ? ` — ${esc(c.location)}` : ""}</option>`).join("");
  if (p.college?.id) college.value = p.college.id;
  else if (!single) college.value = "";

  await fillDepartments(college.value);
  wkModal("wk-profile-modal", true);
  save.disabled = !dept.value;
  return false;
}

async function fillDepartments(collegeId) {
  const dept = document.getElementById("wk-p-department");
  if (!dept) return;
  const hint = document.getElementById("wk-p-hint");
  if (!collegeId) {
    dept.innerHTML = `<option value="">Select your college first…</option>`;
    dept.disabled = true;
    return;
  }
  try {
    const { departments } = await apiFetch(`/api/org/colleges/${collegeId}/departments`);
    dept.disabled = !departments.length;
    if (!departments.length) {
      // Otherwise this is an empty dropdown next to a required field, with
      // nothing on screen saying why it cannot be satisfied.
      dept.innerHTML = `<option value="">No departments in this college yet</option>`;
      if (hint) hint.textContent =
        "That college has no departments yet. Ask an administrator to add yours.";
      return;
    }
    if (hint) hint.textContent = "Pick your teaching department, or your post if you hold one.";
    dept.innerHTML = `<option value="">Select your department or post…</option>` +
      departmentOptions(departments);
    if (WK.profile?.department?.id) dept.value = WK.profile.department.id;
  } catch (err) {
    dept.innerHTML = `<option value="">Could not load departments</option>`;
    dept.disabled = true;
  }
}

document.getElementById("wk-p-college")?.addEventListener("change", async (e) => {
  await fillDepartments(e.target.value);
  document.getElementById("wk-p-save").disabled = true;
});

document.getElementById("wk-p-department")?.addEventListener("change", (e) => {
  document.getElementById("wk-p-save").disabled = !e.target.value;
});

document.getElementById("wk-p-save")?.addEventListener("click", async (e) => {
  const college_id = document.getElementById("wk-p-college").value;
  const department_id = document.getElementById("wk-p-department").value;
  if (!department_id) return showToast("Select your department", "error");

  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch("/api/org/profile", { method: "PUT", body: { college_id, department_id } });
    wkModal("wk-profile-modal", false);
    showToast("Work profile saved", "success");
    await refreshView();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});

/* Position and colour in one place, called from the input handler and again
   after the sheet is rebuilt.

   The stage is a class rather than a CSS comparison because CSS cannot compare
   a custom property to a number -- and hiding the thresholds in three
   selectors would put the rule in the stylesheet while the value lives here.
*/
function paintSlider(slider, pct) {
  slider.style.setProperty("--pct", pct);
  slider.classList.toggle("is-mid", pct >= 50 && pct < 90);
  slider.classList.toggle("is-high", pct >= 90);
}

/* The slider is rebuilt with the sheet, so this is delegated from the document
   rather than bound to an element that will not exist yet. */
document.addEventListener("input", (e) => {
  if (e.target.id !== "wk-my-progress") return;
  const pct = Number(e.target.value);
  const label = document.getElementById("wk-my-progress-value");
  const dirty = document.getElementById("wk-progress-dirty");
  const slider = document.getElementById("wk-my-progress-slider");

  if (label) label.textContent = `${pct}%`;
  // One number drives the fill, the thumb and the colour. Setting three
  // things separately is three chances for them to disagree mid-drag.
  if (slider) paintSlider(slider, pct);

  // Says plainly that the number on screen is not yet the number on the
  // server -- moving a slider looks like it saved, and it does not.
  if (dirty) dirty.classList.toggle("hidden", pct === Number(e.target.dataset.initial));
});

/* While a finger or pointer is down the thumb tracks it exactly; the spring is
   for when it is let go, or when the value is set some other way. A spring
   during the drag would put the thumb behind the finger, which reads as lag
   rather than as bounce. */
document.addEventListener("pointerdown", (e) => {
  if (e.target.id === "wk-my-progress") {
    document.getElementById("wk-my-progress-slider")?.classList.add("is-dragging");
  }
});
document.addEventListener("pointerup", () => {
  document.getElementById("wk-my-progress-slider")?.classList.remove("is-dragging");
});
document.addEventListener("pointercancel", () => {
  document.getElementById("wk-my-progress-slider")?.classList.remove("is-dragging");
});

document.getElementById("wk-t-create")?.addEventListener("click", async (e) => {
  const title = document.getElementById("wk-t-title").value.trim();
  const ids = [...document.querySelectorAll("#wk-t-members input:checked")].map((i) => i.value);
  if (!title) return showToast("Give the task a title", "error");
  if (!ids.length) return showToast("Pick at least one person", "error");

  const due = document.getElementById("wk-t-due").value;
  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch(`/api/work/communities/${document.getElementById("wk-task-community").value}/tasks`, {
      method: "POST",
      body: {
        title,
        description: document.getElementById("wk-t-desc").value.trim() || null,
        priority: document.getElementById("wk-t-priority").value,
        due_date: due ? new Date(due + "T17:00").toISOString() : null,
        assignee_ids: ids,
      },
    });
    wkModal("wk-task-modal", false);
    document.getElementById("wk-t-title").value = "";
    showToast(`Sent to ${ids.length} ${ids.length === 1 ? "person" : "people"} to accept`, "success");
    await refreshView();
  } catch (err) { showToast(err.message, "error"); }
  finally { setButtonLoading(e.currentTarget, false); }
});

registerRefresh(refreshView);

if (document.querySelector(".work-mode")) {
  // The panel comes first: everything below it needs a department to mean
  // anything, and the server refuses the same actions regardless.
  ensureWorkProfile().then((ok) => { if (ok) refreshView(); });
}
