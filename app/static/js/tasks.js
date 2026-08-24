/* Task list, creation, completion, filtering. */

async function loadTasks() {
  const el = document.getElementById("tasks-list");
  const filter = document.getElementById("tk-filter").value;
  try {
    const tasks = await apiFetch(`/api/tasks${filter ? `?status=${filter}` : ""}`);
    el.innerHTML = tasks.length
      ? tasks
          .map(
            (t) => `
        <div class="task-row">
          <div style="flex:1">
            <div class="task-name" style="${t.status === "completed" ? "text-decoration:line-through;opacity:.6" : ""}">${esc(t.title)}</div>
            <div class="muted-text">${t.due_date ? "Due " + fmtDate(t.due_date) : "No due date"}</div>
          </div>
          <span class="pill priority-${t.priority}">${t.priority}</span>
          ${
            t.status !== "completed"
              ? `<button class="btn btn-sm" onclick="completeTask('${t.id}')">✓ Complete</button>`
              : ""
          }
          <button class="btn btn-sm btn-danger" onclick="deleteTask('${t.id}')">Delete</button>
        </div>`
          )
          .join("")
      : `<p class="muted-text">No tasks found.</p>`;
  } catch (_) {
    el.innerHTML = `<p class="muted-text">Could not load tasks.</p>`;
  }
}

async function completeTask(id) {
  try {
    await apiFetch(`/api/tasks/${id}/complete`, { method: "POST" });
    showToast("Task completed", "success");
    loadTasks();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteTask(id) {
  if (!confirm("Delete this task?")) return;
  try {
    await apiFetch(`/api/tasks/${id}`, { method: "DELETE" });
    showToast("Task deleted", "success");
    loadTasks();
  } catch (err) {
    showToast(err.message, "error");
  }
}

document.getElementById("tk-create-btn")?.addEventListener("click", async () => {
  const title = document.getElementById("tk-title").value.trim();
  const due = document.getElementById("tk-due").value;
  const priority = document.getElementById("tk-priority").value;
  if (!title) {
    showToast("Title is required", "error");
    return;
  }
  try {
    await apiFetch("/api/tasks", {
      method: "POST",
      body: { title, due_date: due ? new Date(due).toISOString() : null, priority },
    });
    showToast("Task created", "success");
    document.getElementById("tk-title").value = "";
    document.getElementById("tk-due").value = "";
    loadTasks();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("tk-filter")?.addEventListener("change", loadTasks);
loadTasks();
