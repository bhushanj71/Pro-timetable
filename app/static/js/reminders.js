/* Reminder list + creation. */

async function loadReminders() {
  const el = document.getElementById("reminders-list");
  const includePast = document.getElementById("rm-show-past")?.checked;
  try {
    const reminders = await apiFetch(`/api/reminders${includePast ? "?include_past=true" : ""}`);
    el.innerHTML = reminders.length
      ? reminders
          .map(
            (r) => `
        <div class="task-row">
          <div class="tl-time">${fmtDate(r.reminder_datetime)}<br>${fmtTime(r.reminder_datetime)}</div>
          <div style="flex:1">
            <div class="task-name">${r.title || "Reminder"}</div>
            <div class="muted-text">${r.reminder_type} · ${r.is_sent ? "Sent" : "Pending"}</div>
          </div>
          <button class="btn btn-sm btn-danger" onclick="deleteReminder('${r.id}')">Delete</button>
        </div>`
          )
          .join("")
      : `<p class="muted-text">No reminders yet.</p>`;
  } catch (_) {
    el.innerHTML = `<p class="muted-text">Could not load reminders.</p>`;
  }
}

async function deleteReminder(id) {
  try {
    await apiFetch(`/api/reminders/${id}`, { method: "DELETE" });
    showToast("Reminder deleted", "success");
    loadReminders();
  } catch (err) {
    showToast(err.message, "error");
  }
}

document.getElementById("rm-create-btn")?.addEventListener("click", async () => {
  const title = document.getElementById("rm-title").value.trim();
  const when = document.getElementById("rm-when").value;
  const type = document.getElementById("rm-type").value;
  if (!title || !when) {
    showToast("Title and time are required", "error");
    return;
  }
  try {
    await apiFetch("/api/reminders", {
      method: "POST",
      body: { title, reminder_datetime: new Date(when).toISOString(), reminder_type: type },
    });
    showToast("Reminder created", "success");
    document.getElementById("rm-title").value = "";
    document.getElementById("rm-when").value = "";
    loadReminders();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("rm-show-past")?.addEventListener("change", loadReminders);
loadReminders();
