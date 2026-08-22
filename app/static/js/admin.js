/* Admin control panel: system stats and full user management. */

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
  } catch (err) {
    showToast(err.message || "Could not load stats", "error");
  }
}

async function loadUsers() {
  const tbody = document.getElementById("ad-user-tbody");
  const q = document.getElementById("ad-search").value.trim();
  try {
    const users = await apiFetch(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    if (!users.length) {
      tbody.innerHTML = `<tr><td colspan="8" style="padding:12px" class="schedule-sub">No users found.</td></tr>`;
      return;
    }
    tbody.innerHTML = users
      .map(
        (u) => `
      <tr style="border-bottom:1px solid var(--color-border)">
        <td style="padding:8px"><strong>${escapeHtml(u.name)}</strong></td>
        <td style="padding:8px">${escapeHtml(u.email)}</td>
        <td style="padding:8px">${escapeHtml(u.department || "—")}</td>
        <td style="padding:8px"><span class="badge-pill ${u.is_admin ? "priority-urgent" : "priority-medium"}">${u.is_admin ? "Admin" : "Professor"}</span></td>
        <td style="padding:8px"><span class="badge-pill ${u.is_active ? "priority-low" : "priority-high"}">${u.is_active ? "Active" : "Disabled"}</span></td>
        <td style="padding:8px">${u.event_count}</td>
        <td style="padding:8px">${u.last_login_at ? fmtDate(u.last_login_at) : "Never"}</td>
        <td style="padding:8px;white-space:nowrap">
          <button class="btn btn-sm" data-act="edit" data-id="${u.id}">Edit</button>
          <button class="btn btn-sm" data-act="pw" data-id="${u.id}" data-email="${escapeHtml(u.email)}">Reset PW</button>
          <button class="btn btn-sm btn-danger" data-act="del" data-id="${u.id}" data-email="${escapeHtml(u.email)}">Delete</button>
        </td>
      </tr>`
      )
      .join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" style="padding:12px" class="schedule-sub">Could not load users.</td></tr>`;
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
