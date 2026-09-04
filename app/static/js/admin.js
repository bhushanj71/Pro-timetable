/* Admin control panel: statistics and user management.

   The same page serves a super admin and a college admin. Nothing here is a
   permission check -- the server scopes every response and refuses every
   out-of-scope write on its own. What this file does is stop the panel
   offering an action that is going to be refused, which is a courtesy to the
   person using it, not a control. */

const AD_ROLE = (document.getElementById("ad-head")?.dataset.role) || "college_admin";
const AD_IS_SUPER = AD_ROLE === "super_admin";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

async function loadStats() {
  try {
    const s = await apiFetch("/api/admin/stats");
    const map = {
      "ad-total-users": s.total_users,
      "ad-active-users": s.active_users,
      "ad-total-events": s.total_events,
      "ad-pending-reminders": s.pending_reminders,
      "ad-admin-users": s.admin_users,
      "ad-total-tasks": s.total_tasks,
      "ad-ai-convos": s.ai_conversations,
      "ad-new-users": s.new_users_this_week,
    };
    Object.entries(map).forEach(([id, v]) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    });
    /* "Total Users" is a true statement for a super admin and a false one for
       a college admin, who is being shown their college. The honest label is
       carried on the element rather than swapped by id, so a new tile gets
       the behaviour by adding an attribute. */
    if (!AD_IS_SUPER) {
      document.querySelectorAll("[data-scoped]").forEach((el) => {
        el.textContent = el.dataset.scoped;
      });
    }
  } catch (err) {
    showToast(err.message || "Could not load stats", "error");
  }
}

/* Three roles, not two. A college admin shown as "Professor" is the panel
   hiding the thing an administrator most needs to see: who else has power. */
function roleCell(u) {
  if (u.is_admin) return `<span class="pill priority-urgent">Super Admin</span>`;
  if (u.admin_college_id) {
    return `<span class="pill priority-high" title="Administers ${escapeHtml(u.admin_college || "a college")}">College Admin</span>`;
  }
  return `<span class="pill priority-medium">Professor</span>`;
}

function actionCell(u) {
  const email = escapeHtml(u.email);
  /* Visible but not editable: another administrator, seen by a college admin.
     Saying why beats three buttons that return 403. */
  if (!u.manageable) {
    return `<span class="muted-text">Managed by a super admin</span>`;
  }

  let html =
    `<button class="btn btn-sm" data-act="edit" data-id="${u.id}">Edit</button>` +
    `<button class="btn btn-sm" data-act="pw" data-id="${u.id}" data-email="${email}">Reset PW</button>`;

  /* Appointing is a super admin's act, so the control only exists for one. */
  if (AD_IS_SUPER && !u.is_admin) {
    if (u.admin_college_id) {
      html += `<button class="btn btn-sm" data-act="revoke-ca" data-id="${u.id}"` +
              ` data-name="${escapeHtml(u.name)}" data-college="${escapeHtml(u.admin_college || "")}">Stand down</button>`;
    } else if (u.college_id) {
      html += `<button class="btn btn-sm" data-act="grant-ca" data-id="${u.id}"` +
              ` data-college-id="${u.college_id}" data-name="${escapeHtml(u.name)}"` +
              ` data-college="${escapeHtml(u.college || "")}">Make college admin</button>`;
    } else {
      /* No college means nothing to put them in charge of. Explaining that in
         place is more use than a button that returns 400. */
      html += `<span class="muted-text" title="They have not chosen a college yet">No college</span>`;
    }
  }

  html += `<button class="btn btn-sm btn-danger" data-act="del" data-id="${u.id}" data-email="${email}">Delete</button>`;
  return html;
}

async function loadUsers() {
  const tbody = document.getElementById("ad-user-tbody");
  const q = document.getElementById("ad-search").value.trim();
  try {
    const users = await apiFetch(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    if (!users.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="muted-text">No users found.</td></tr>`;
      return;
    }
    tbody.innerHTML = users
      .map(
        (u) => `
      <tr >
        <td ><strong>${escapeHtml(u.name)}</strong></td>
        <td >${escapeHtml(u.email)}</td>
        <td >${escapeHtml(u.department || "—")}</td>
        <td >${roleCell(u)}</td>
        <td ><span class="pill ${u.is_active ? "priority-low" : "priority-high"}">${u.is_active ? "Active" : "Disabled"}</span></td>
        <td >${u.event_count}</td>
        <td >${u.last_login_at ? fmtDate(u.last_login_at) : "Never"}</td>
        <td style="padding:8px;white-space:nowrap">${actionCell(u)}</td>
      </tr>`
      )
      .join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted-text">Could not load users.</td></tr>`;
  }
}

/* ---------------- Modal handling ---------------- */
function openUserModal(user) {
  document.getElementById("ad-modal-title").textContent = user ? "Edit User" : "New User";
  document.getElementById("ad-user-id").value = user?.id || "";
  document.getElementById("ad-name").value = user?.name || "";
  document.getElementById("ad-email").value = user?.email || "";
  document.getElementById("ad-department").value = user?.department || "";
  document.getElementById("ad-designation").value = user?.designation || "";
  document.getElementById("ad-is-admin").value = String(!!user?.is_admin);
  document.getElementById("ad-is-active").value = String(user ? !!user.is_active : true);
  // Password is only set at creation; editing uses the dedicated reset flow.
  document.getElementById("ad-password-group").classList.toggle("hidden", !!user);
  document.getElementById("ad-status-group").classList.toggle("hidden", !user);
  document.getElementById("ad-password").value = "";
  document.getElementById("ad-user-modal").classList.remove("hidden");
}

document.getElementById("ad-new-user-btn")?.addEventListener("click", () => openUserModal(null));
document.getElementById("ad-cancel")?.addEventListener("click", () =>
  document.getElementById("ad-user-modal").classList.add("hidden")
);

document.getElementById("ad-save")?.addEventListener("click", async () => {
  const id = document.getElementById("ad-user-id").value;
  const base = {
    name: document.getElementById("ad-name").value.trim(),
    email: document.getElementById("ad-email").value.trim(),
    department: document.getElementById("ad-department").value.trim() || null,
    designation: document.getElementById("ad-designation").value.trim() || null,
    is_admin: document.getElementById("ad-is-admin").value === "true",
  };
  if (!base.name || !base.email) {
    showToast("Name and email are required", "error");
    return;
  }

  try {
    if (id) {
      await apiFetch(`/api/admin/users/${id}`, {
        method: "PUT",
        body: { ...base, is_active: document.getElementById("ad-is-active").value === "true" },
      });
      showToast("User updated", "success");
    } else {
      const password = document.getElementById("ad-password").value;
      if (password.length < 8) {
        showToast("Password must be at least 8 characters", "error");
        return;
      }
      await apiFetch("/api/admin/users", { method: "POST", body: { ...base, password } });
      showToast("User created", "success");
    }
    document.getElementById("ad-user-modal").classList.add("hidden");
    loadUsers();
    loadStats();
  } catch (err) {
    showToast(err.message, "error");
  }
});

/* ---------------- Password reset ---------------- */
document.getElementById("ad-pw-cancel")?.addEventListener("click", () =>
  document.getElementById("ad-pw-modal").classList.add("hidden")
);

document.getElementById("ad-pw-save")?.addEventListener("click", async () => {
  const id = document.getElementById("ad-pw-user-id").value;
  const pw = document.getElementById("ad-new-password").value;
  if (pw.length < 8) {
    showToast("Password must be at least 8 characters", "error");
    return;
  }
  try {
    await apiFetch(`/api/admin/users/${id}/reset-password`, { method: "POST", body: { new_password: pw } });
    showToast("Password reset", "success");
    document.getElementById("ad-pw-modal").classList.add("hidden");
    document.getElementById("ad-new-password").value = "";
  } catch (err) {
    showToast(err.message, "error");
  }
});

/* ---------------- Row action delegation ---------------- */
document.getElementById("ad-user-tbody")?.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const { act, id, email } = btn.dataset;

  if (act === "edit") {
    try {
      openUserModal(await apiFetch(`/api/admin/users/${id}`));
    } catch (err) {
      showToast(err.message, "error");
    }
  } else if (act === "pw") {
    document.getElementById("ad-pw-user-id").value = id;
    document.getElementById("ad-pw-target").textContent = `Setting a new password for ${email}`;
    document.getElementById("ad-pw-modal").classList.remove("hidden");
  } else if (act === "grant-ca") {
    /* Spelled out, because this is a grant of power rather than an edit: the
       confirm names the person, the college, and the limits of what they are
       being given. */
    const { name, college, collegeId } = btn.dataset;
    if (!confirm(
      "Make " + name + " an administrator of " + college + "?\n\n" +
      "They will get the admin panel for " + college + " only: its members, " +
      "its departments and its figures. They will not see other colleges, " +
      "change anyone's administrator rights, or appoint anybody."
    )) return;
    try {
      await apiFetch(`/api/admin/users/${id}/college-admin`, {
        method: "POST", body: { college_id: collegeId },
      });
      showToast(name + " now administers " + college, "success");
      loadUsers();
      loadStats();
    } catch (err) {
      showToast(err.message, "error");
    }
  } else if (act === "revoke-ca") {
    const { name, college } = btn.dataset;
    if (!confirm(
      "Stand " + name + " down as administrator of " + college + "?\n\n" +
      "Their account and their place in the college are untouched — only " +
      "the admin panel goes away."
    )) return;
    try {
      await apiFetch(`/api/admin/users/${id}/college-admin`, { method: "DELETE" });
      showToast(name + " is no longer a college admin", "success");
      loadUsers();
      loadStats();
    } catch (err) {
      showToast(err.message, "error");
    }
  } else if (act === "del") {
    if (!confirm(`Delete ${email}?\n\nThis permanently removes their events, tasks, reminders, and AI history. This cannot be undone.`)) return;
    try {
      await apiFetch(`/api/admin/users/${id}`, { method: "DELETE" });
      showToast("User deleted", "success");
      loadUsers();
      loadStats();
    } catch (err) {
      showToast(err.message, "error");
    }
  }
});

let adSearchDebounce;
document.getElementById("ad-search")?.addEventListener("input", () => {
  clearTimeout(adSearchDebounce);
  adSearchDebounce = setTimeout(loadUsers, 250);
});

loadStats();
loadUsers();


/* ==========================================================================
   Organisation: colleges and the departments under them

   Every button here is also checked on the server. A hidden control is a
   courtesy to the person looking at the screen, not a permission.
   ========================================================================== */
const ORG = { role: null, canCreate: false, college: null };

async function loadColleges() {
  const box = document.getElementById("ad-college-list");
  if (!box) return;
  try {
    const d = await apiFetch("/api/org/manage/colleges");
    ORG.role = d.role;
    ORG.canCreate = d.can_create_college;

    document.getElementById("ad-org-role").textContent =
      d.role === "super_admin"
        ? "You manage every college on this deployment."
        : "You manage your own college.";
    document.getElementById("ad-add-college").classList.toggle("hidden", !d.can_create_college);

    box.innerHTML = d.colleges.length
      ? d.colleges.map((c) => `
          <div class="ad-college${c.status === "archived" ? " is-archived" : ""}">
            <div class="ad-college-main">
              <div class="ad-college-name">🏫 ${esc(c.name)}
                ${c.status === "archived" ? `<span class="wk-chip declined">Archived</span>` : ""}
              </div>
              <div class="ad-college-meta">
                ${c.department_count} department${c.department_count === 1 ? "" : "s"}
                · ${c.member_count} member${c.member_count === 1 ? "" : "s"}
                ${c.location ? ` · ${esc(c.location)}` : ""}
              </div>
            </div>
            <div class="ad-college-actions">
              <button class="btn btn-sm" data-edit-college="${c.id}">✏️ Edit</button>
              <button class="btn btn-sm btn-primary" data-manage-depts="${c.id}">Manage →</button>
            </div>
          </div>`).join("")
      : `<div class="wk-empty">No colleges yet.</div>`;
  } catch (err) {
    box.innerHTML = `<div class="wk-empty">${esc(err.message)}</div>`;
  }
}

async function openDepartments(collegeId) {
  ORG.college = collegeId;
  const box = document.getElementById("ad-dept-list");
  document.getElementById("ad-dept-modal").classList.remove("hidden");
  box.innerHTML = `<div class="wk-empty">Loading…</div>`;
  try {
    const d = await apiFetch(`/api/org/manage/colleges/${collegeId}/departments`);
    document.getElementById("ad-dept-college").textContent = `🏫 ${d.college.name}`;

    box.innerHTML = d.departments.length
      ? d.departments.map((x) => {
          const archived = x.status === "archived";
          return `
          <div class="ad-dept${archived ? " is-archived" : ""}">
            <div class="ad-dept-main">
              <span class="ad-dept-name">${esc(x.name)}</span>
              <span class="ad-dept-meta">${x.member_count} member${x.member_count === 1 ? "" : "s"}</span>
              ${archived ? `<span class="wk-chip declined">Archived</span>` : ""}
            </div>
            <div class="ad-dept-actions">
              <button class="chip-x" data-rename-dept="${x.id}" data-name="${esc(x.name)}" title="Rename" aria-label="Rename ${esc(x.name)}">✏️</button>
              <button class="chip-x" data-archive-dept="${x.id}" data-to="${archived ? "active" : "archived"}"
                      title="${archived ? "Restore" : "Archive"}" aria-label="${archived ? "Restore" : "Archive"} ${esc(x.name)}">${archived ? "♻️" : "📦"}</button>
              ${x.member_count === 0
                ? `<button class="chip-x" data-delete-dept="${x.id}" data-name="${esc(x.name)}" title="Delete" aria-label="Delete ${esc(x.name)}">🗑️</button>`
                : ""}
            </div>
          </div>`;
        }).join("")
      : `<div class="wk-empty">No departments yet.</div>`;
  } catch (err) {
    box.innerHTML = `<div class="wk-empty">${esc(err.message)}</div>`;
  }
}

document.getElementById("ad-add-college")?.addEventListener("click", () => {
  document.getElementById("ad-college-id").value = "";
  document.getElementById("ad-college-name").value = "";
  document.getElementById("ad-college-loc").value = "";
  document.getElementById("ad-college-title").textContent = "🏫 Add New College";
  document.getElementById("ad-college-save").textContent = "+ Add College";
  document.getElementById("ad-college-modal").classList.remove("hidden");
});

document.getElementById("ad-college-save")?.addEventListener("click", async (e) => {
  const id = document.getElementById("ad-college-id").value;
  const name = document.getElementById("ad-college-name").value.trim();
  const location = document.getElementById("ad-college-loc").value.trim() || null;
  if (!name) return showToast("Give the college a name", "error");

  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch(id ? `/api/org/colleges/${id}` : "/api/org/colleges",
      { method: id ? "PUT" : "POST", body: { name, location } });
    document.getElementById("ad-college-modal").classList.add("hidden");
    showToast(id ? "College updated" : "College added", "success");
    await loadColleges();
  } catch (err) { showToast(err.message, "error"); }
  finally { setButtonLoading(e.currentTarget, false); }
});

document.getElementById("ad-dept-add")?.addEventListener("click", async (e) => {
  const input = document.getElementById("ad-dept-new");
  const name = input.value.trim();
  if (!name) return showToast("Give the department a name", "error");

  setButtonLoading(e.currentTarget, true);
  try {
    await apiFetch("/api/org/departments",
      { method: "POST", body: { college_id: ORG.college, name } });
    input.value = "";
    showToast("Department added", "success");
    await openDepartments(ORG.college);
    await loadColleges();
  } catch (err) { showToast(err.message, "error"); }
  finally { setButtonLoading(e.currentTarget, false); }
});

document.addEventListener("click", async (e) => {
  const manage = e.target.closest("[data-manage-depts]");
  if (manage) return openDepartments(manage.dataset.manageDepts);

  const edit = e.target.closest("[data-edit-college]");
  if (edit) {
    const row = edit.closest(".ad-college");
    document.getElementById("ad-college-id").value = edit.dataset.editCollege;
    document.getElementById("ad-college-name").value =
      row.querySelector(".ad-college-name").textContent.replace("🏫", "").replace("Archived", "").trim();
    document.getElementById("ad-college-loc").value = "";
    document.getElementById("ad-college-title").textContent = "🏫 Edit College";
    document.getElementById("ad-college-save").textContent = "Save";
    document.getElementById("ad-college-modal").classList.remove("hidden");
    return;
  }

  const rename = e.target.closest("[data-rename-dept]");
  if (rename) {
    const name = prompt("Department name", rename.dataset.name);
    if (!name || name === rename.dataset.name) return;
    try {
      await apiFetch(`/api/org/departments/${rename.dataset.renameDept}`,
        { method: "PUT", body: { name } });
      showToast("Renamed", "success");
      await openDepartments(ORG.college);
    } catch (err) { showToast(err.message, "error"); }
    return;
  }

  const archive = e.target.closest("[data-archive-dept]");
  if (archive) {
    try {
      await apiFetch(`/api/org/departments/${archive.dataset.archiveDept}`,
        { method: "PUT", body: { status: archive.dataset.to } });
      showToast(archive.dataset.to === "archived" ? "Archived" : "Restored", "success");
      await openDepartments(ORG.college);
      await loadColleges();
    } catch (err) { showToast(err.message, "error"); }
    return;
  }

  const del = e.target.closest("[data-delete-dept]");
  if (del) {
    if (!confirm(`Delete "${del.dataset.name}"? This cannot be undone.`)) return;
    try {
      await apiFetch(`/api/org/departments/${del.dataset.deleteDept}`, { method: "DELETE" });
      showToast("Deleted", "success");
      await openDepartments(ORG.college);
      await loadColleges();
    } catch (err) { showToast(err.message, "error"); }
  }
});

document.querySelectorAll("[data-close-modal]").forEach((b) =>
  b.addEventListener("click", () =>
    document.getElementById(b.dataset.closeModal)?.classList.add("hidden")));

loadColleges();
