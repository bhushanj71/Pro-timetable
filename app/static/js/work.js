/* Work mode: communities, assignments, progress.

   Reads go through the same cache as the rest of the app, under work: keys so
   nothing here can be served from a personal-mode entry or vice versa. */

const wkModal = (id, open = true) =>
  document.getElementById(id)?.classList.toggle("hidden", !open);

document.querySelectorAll("[data-close-modal]").forEach((b) =>
  b.addEventListener("click", () => wkModal(b.dataset.closeModal, false))
);

const PRIORITY_DOT = { low: "🟢", medium: "🟡", high: "🟠", urgent: "🔴" };
const STATUS_LABEL = {
  pending: "Awaiting your answer",
  accepted: "Accepted",
  in_progress: "In progress",
  completed: "Completed",
  declined: "Declined",
};

function bar(pct, cls = "") {
  return `<div class="wk-bar ${cls}"><span style="width:${Math.max(0, Math.min(100, pct))}%"></span></div>`;
}

function personRow(a) {
  const pending = a.status === "pending";
  const declined = a.status === "declined";
  return `
    <div class="wk-person">
      <span class="wk-avatar">${esc(a.user.initial)}</span>
      <span class="wk-person-name">${esc(a.user.name)}</span>
      ${pending ? `<span class="wk-chip pending">⏳ Not answered yet</span>`
        : declined ? `<span class="wk-chip declined">✕ Declined${a.decline_reason ? " · " + esc(a.decline_reason) : ""}</span>`
        : `<span class="wk-person-bar">${bar(a.progress)}</span><span class="wk-pct">${a.progress}%</span>`}
    </div>`;
}

/* ---------------- Dashboard ---------------- */
async function loadWork() {
  const [data, invites] = await Promise.all([
    cachedFetch("/api/work/dashboard"),
    cachedFetch("/api/work/invitations"),
  ]);

  document.getElementById("wk-n-active").textContent = data.counts.active;
  document.getElementById("wk-n-pending").textContent = data.counts.pending;
  document.getElementById("wk-n-done").textContent = data.counts.completed;

  // --- Community invitations ---
  const invBox = document.getElementById("wk-invites");
  invBox.innerHTML = invites.invitations.map((i) => `
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
  const reqBox = document.getElementById("wk-requests");
  reqBox.innerHTML = data.requests.map((t) => `
    <div class="wk-request">
      <div class="wk-request-main">
        <div class="wk-request-title">${PRIORITY_DOT[t.priority] || ""} ${esc(t.title)}</div>
        <div class="wk-request-meta">
          ${esc(t.creator.name)} · ${esc(t.community.name)}${t.due_date ? " · due " + fmtDate(t.due_date) : ""}
        </div>
      </div>
      <div class="wk-request-actions">
        <button class="btn btn-sm btn-primary" data-task-respond="${t.id}" data-accept="1">✓ Accept</button>
        <button class="btn btn-sm" data-task-respond="${t.id}" data-accept="0">✕ Decline</button>
      </div>
    </div>`).join("");

  if (!invites.invitations.length && !data.requests.length) {
    reqBox.innerHTML = `<div class="wk-empty">Nothing needs your answer right now.</div>`;
  }

  // --- My active tasks ---
  document.getElementById("wk-active").innerHTML = data.active_tasks.length
    ? data.active_tasks.map((t) => `
        <div class="wk-task" data-open-task="${t.id}">
          <div class="wk-task-head">
            <span class="wk-task-title">${PRIORITY_DOT[t.priority] || ""} ${esc(t.title)}</span>
            <span class="wk-pct">${t.my_progress}%</span>
          </div>
          ${bar(t.my_progress)}
          <div class="wk-task-meta">${esc(t.community.name)}${t.due_date ? " · due " + fmtDate(t.due_date) : ""}</div>
        </div>`).join("")
    : `<div class="wk-empty">No active tasks. Accepted work appears here.</div>`;

  // --- Communities ---
  document.getElementById("wk-communities").innerHTML = data.communities.length
    ? data.communities.map((c) => `
        <div class="wk-community" data-open-community="${c.id}">
          <span class="wk-community-icon">${esc(c.icon)}</span>
          <div style="flex:1;min-width:0">
            <div class="wk-community-name">${esc(c.name)}</div>
            <div class="wk-task-meta">${c.member_count} member${c.member_count === 1 ? "" : "s"} · ${esc(c.my_role)}</div>
          </div>
        </div>`).join("")
    : `<div class="wk-empty">No communities yet. Create one to start assigning work.</div>`;

  loadCreatedTasks();
}

/* ---------------- Tasks I assigned ---------------- */
async function loadCreatedTasks() {
  const box = document.getElementById("wk-created");
  const { tasks } = await cachedFetch("/api/work/tasks?scope=created");
  box.innerHTML = tasks.length
    ? tasks.map((t) => {
        const p = t.progress;
        return `
        <div class="wk-task" data-open-task="${t.id}">
          <div class="wk-task-head">
            <span class="wk-task-title">${PRIORITY_DOT[t.priority] || ""} ${esc(t.title)}</span>
            <span class="wk-pct">${p.overall}%</span>
          </div>
          ${bar(p.overall)}
          <div class="wk-task-meta">
            ${p.accepted} accepted${p.pending ? ` · ${p.pending} pending` : ""}${p.declined ? ` · ${p.declined} declined` : ""}
            · ${esc(t.community.name)}
          </div>
        </div>`;
      }).join("")
    : `<div class="wk-empty">Nothing assigned yet.</div>`;
}

/* ---------------- Task detail ---------------- */
async function openTask(taskId) {
  const body = document.getElementById("wk-taskdetail-body");
  wkModal("wk-taskdetail-modal", true);
  body.innerHTML = `<div class="sk sk-line long"></div><div class="sk sk-line medium"></div>`;

  const t = await cachedFetch(`/api/work/tasks/${taskId}`, { force: true });
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

    <div class="wk-people">${t.assignments.map(personRow).join("")}</div>

    ${me && ["accepted", "in_progress", "completed"].includes(me.status) ? `
      <div class="form-group" style="margin-top:16px">
        <label for="wk-my-progress">My progress — ${me.progress}%</label>
        <input type="range" id="wk-my-progress" min="0" max="100" step="5" value="${me.progress}">
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
}

/* ---------------- Community detail ---------------- */
async function openCommunity(id) {
  const body = document.getElementById("wk-detail-body");
  wkModal("wk-detail-modal", true);
  body.innerHTML = `<div class="sk sk-line long"></div><div class="sk sk-line medium"></div>`;

  const c = await cachedFetch(`/api/work/communities/${id}`, { force: true });
  const canInvite = c.my_role === "owner" || c.my_role === "admin";

  body.innerHTML = `
    <h3 style="margin:0 0 4px">${esc(c.icon)} ${esc(c.name)}</h3>
    <p class="muted-text" style="margin:0 0 14px">${esc(c.description || "No description")}</p>

    <div class="btn-row" style="margin-bottom:14px">
      <button class="btn btn-sm btn-primary" data-assign-in="${c.id}">+ Assign a task</button>
      ${canInvite ? `<button class="btn btn-sm" data-invite-to="${c.id}">+ Invite someone</button>` : ""}
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

    <div class="wk-people">
      ${c.members.map((m) => `
        <div class="wk-person">
          <span class="wk-avatar">${esc(m.initial)}</span>
          <span class="wk-person-name">${esc(m.name)}</span>
          <span class="wk-chip role">${esc(m.role)}</span>
          ${canInvite && m.role !== "owner" ? `<button class="chip-x" data-remove-member="${m.id}" data-community="${c.id}" aria-label="Remove">✕</button>` : ""}
        </div>`).join("")}
      ${c.pending_invites.map((p) => `
        <div class="wk-person">
          <span class="wk-avatar">${esc(p.initial)}</span>
          <span class="wk-person-name">${esc(p.name)}</span>
          <span class="wk-chip pending">⏳ Invited</span>
        </div>`).join("")}
    </div>`;
}

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
      await loadWork();
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
      wkModal("wk-taskdetail-modal", false);
      await loadWork();
    } catch (err) { showToast(err.message, "error"); setButtonLoading(respond, false); }
    return;
  }

  const openT = t.closest("[data-open-task]");
  if (openT) return openTask(openT.dataset.openTask);

  const openC = t.closest("[data-open-community]");
  if (openC) return openCommunity(openC.dataset.openCommunity);

  if (t.closest("[data-invite-to]")) {
    document.getElementById("wk-invite-row")?.classList.remove("hidden");
    return;
  }

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
      await loadWork();
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
      await loadWork();
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
  document.getElementById("wk-t-members").innerHTML = c.members.map((m) => `
    <label class="wk-pick">
      <input type="checkbox" value="${m.id}"> <span class="wk-avatar">${esc(m.initial)}</span> ${esc(m.name)}
    </label>`).join("");
  wkModal("wk-detail-modal", false);
  wkModal("wk-task-modal", true);
}

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
    await loadWork();
  } catch (err) { showToast(err.message, "error"); }
  finally { setButtonLoading(e.currentTarget, false); }
});

if (document.querySelector(".work-mode")) loadWork();
