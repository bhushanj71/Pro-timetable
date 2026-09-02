/* The work board: metrics, a row per member, and one member's history.

   Reuses work.js for everything it can -- esc, bar, apiFetch, showToast,
   fmtDate -- and adds only what is genuinely new here. Task rows link to
   /work/task/<id>, the page that already exists, rather than re-implementing
   a task view on this one. */
(function () {
  const root = document.querySelector(".wk-board");
  if (!root) return;

  const COMMUNITY = root.dataset.community;
  const BOARD = { member: null, performance: [] };

  const BUCKET_LABEL = {
    pending: "Pending",
    in_progress: "In progress",
    completed: "Completed",
    incomplete: "Incomplete",
    overdue: "Overdue",
  };

  /* ---------------- Metrics ---------------- */
  function paintMetrics(data) {
    document.getElementById("wk-board-name").textContent = `${data.community.icon} ${data.community.name}`;
    const t = data.totals;
    // Ordered the way the question is asked: how many of us, how much is
    // live, how much is done, and then the two that need acting on.
    const tiles = [
      ["Members", t.members, "info"],
      ["Active tasks", t.active, "active"],
      ["Completed", t.completed, "done"],
      ["Incomplete", t.incomplete, "pending"],
      ["Overdue", t.overdue, "alert"],
    ];
    document.getElementById("wk-metrics").innerHTML = tiles.map(([label, value, tone]) => `
      <div class="wk-metric" data-tone="${tone}">
        <div class="wk-metric-n">${value}</div>
        <div class="wk-metric-l">${esc(label)}</div>
      </div>`).join("");
  }

  /* ---------------- Member performance ----------------
     A table on a desktop and cards on a phone, from one markup: the cells
     carry their own labels and CSS shows them only when the table collapses.
     Two renderers would be two things to keep in step. */
  function paintPerformance(rows) {
    BOARD.performance = rows;
    const box = document.getElementById("wk-performance");
    if (!rows.length) {
      box.innerHTML = `<div class="wk-empty">Nobody has joined this community yet.</div>`;
      return;
    }
    box.innerHTML = `
      <div class="wk-table-wrap">
        <table class="wk-table">
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col" class="num">Assigned</th>
              <th scope="col" class="num">Completed</th>
              <th scope="col" class="num">In progress</th>
              <th scope="col" class="num">Incomplete</th>
              <th scope="col" class="num">Overdue</th>
              <th scope="col">Completion</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((r) => `
              <tr class="wk-row" data-member="${esc(r.user_id)}" tabindex="0" role="button"
                  aria-label="See ${esc(r.user.name)}'s work">
                <td data-label="Member">
                  <div class="wk-person-name">${esc(r.user.name)}</div>
                  ${r.user.department ? `<div class="wk-id-dept">🏢 ${esc(r.user.department)}</div>` : ""}
                </td>
                <td class="num" data-label="Assigned">${r.assigned}</td>
                <td class="num" data-label="Completed">${r.completed}</td>
                <td class="num" data-label="In progress">${r.in_progress}</td>
                <td class="num" data-label="Incomplete">${r.incomplete}</td>
                <td class="num" data-label="Overdue">${r.overdue ? `<span class="wk-late">${r.overdue}</span>` : 0}</td>
                <td data-label="Completion">
                  <div class="wk-inline-bar">${bar(r.completion)}<span class="wk-pct">${r.completion}%</span></div>
                </td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
  }

  /* ---------------- One member's history ---------------- */
  function taskRowMarkup(t) {
    const late = t.bucket === "overdue";
    return `
      <a class="wk-track" href="/work/task/${encodeURIComponent(t.id)}">
        <div class="wk-track-head">
          <span class="wk-dot" data-p="${esc(t.priority || "medium")}"></span>
          <div class="wk-track-main">
            <div class="wk-task-title">${esc(t.title)}</div>
            <div class="wk-task-meta">
              ${esc(t.member.name)} · assigned by ${esc(t.assigned_by.name)}
              ${t.due_date ? ` · due ${fmtDate(t.due_date)}` : ""}
              ${t.completed_at ? ` · finished ${fmtDate(t.completed_at)}` : ""}
            </div>
          </div>
          <span class="wk-badge ${esc(t.bucket)}">${esc(BUCKET_LABEL[t.bucket] || t.bucket)}</span>
        </div>
        <div class="wk-inline-bar">${bar(t.progress, late ? "late" : "")}<span class="wk-pct">${t.progress}%</span></div>
        ${t.attachment_count
          ? `<div class="wk-track-atts">📎 ${t.attachment_count} attachment${t.attachment_count === 1 ? "" : "s"}</div>`
          : ""}
        ${t.decline_reason ? `<div class="wk-track-note">✕ Declined · ${esc(t.decline_reason)}</div>` : ""}
      </a>`;
  }

  async function loadMember(memberId) {
    BOARD.member = memberId;
    const card = document.getElementById("wk-member-card");
    const list = document.getElementById("wk-member-tasks");
    card.classList.remove("hidden");
    list.innerHTML = `<div class="sk sk-line long"></div><div class="sk sk-line medium"></div>`;

    const params = new URLSearchParams();
    const status = document.getElementById("wk-f-status").value;
    const period = document.getElementById("wk-f-period").value;
    if (status) params.set("status", status);
    if (period) params.set("period", period);
    if (period === "custom") {
      const start = document.getElementById("wk-f-start").value;
      const end = document.getElementById("wk-f-end").value;
      // A custom range with neither end set is "all time" wearing a costume.
      if (!start && !end) params.delete("period");
      if (start) params.set("start", start);
      if (end) params.set("end", end);
    }

    try {
      const data = await apiFetch(
        `/api/work/communities/${COMMUNITY}/members/${memberId}/tasks?${params}`
      );
      document.getElementById("wk-member-name").textContent = data.member.name;
      const t = data.tally;
      // The tally always describes everything they hold. The list above it is
      // what the filters left, and saying so stops the two reading as a
      // contradiction.
      document.getElementById("wk-member-tally").textContent =
        `${t.assigned} assigned · ${t.completed} completed · ${t.in_progress} in progress` +
        `${t.overdue ? ` · ${t.overdue} overdue` : ""} · ${t.completion}% completion`;

      list.innerHTML = data.tasks.length
        ? data.tasks.map(taskRowMarkup).join("")
        : `<div class="wk-empty">Nothing matches those filters${
            data.period !== "All time" ? ` in ${data.period.toLowerCase()}` : ""
          }.</div>`;
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      list.innerHTML = `<div class="wk-empty">${esc(err.message)}</div>`;
    }
  }

  /* ---------------- Search ---------------- */
  let searchTimer;
  document.getElementById("wk-board-q")?.addEventListener("input", (e) => {
    const q = e.target.value.trim();
    document.getElementById("wk-board-q-clear").hidden = !q;
    clearTimeout(searchTimer);
    // Debounced, because this one does go to the server -- unlike the Work
    // dashboard's search, which filters what is already loaded.
    searchTimer = setTimeout(async () => {
      const box = document.getElementById("wk-board-results");
      if (!q) { box.innerHTML = ""; return; }
      box.innerHTML = `<div class="sk sk-line medium"></div>`;
      try {
        const { results } = await apiFetch(
          `/api/work/communities/${COMMUNITY}/search?q=${encodeURIComponent(q)}`
        );
        box.innerHTML = results.length
          ? results.map(taskRowMarkup).join("")
          : `<div class="wk-empty">Nothing matches “${esc(q)}”.</div>`;
      } catch (err) {
        box.innerHTML = `<div class="wk-empty">${esc(err.message)}</div>`;
      }
    }, 250);
  });

  document.getElementById("wk-board-q-clear")?.addEventListener("click", () => {
    const input = document.getElementById("wk-board-q");
    input.value = "";
    document.getElementById("wk-board-results").innerHTML = "";
    document.getElementById("wk-board-q-clear").hidden = true;
    input.focus();
  });

  /* ---------------- Wiring ---------------- */
  document.addEventListener("click", (e) => {
    const row = e.target.closest(".wk-row[data-member]");
    if (row) loadMember(row.dataset.member);
    if (e.target.closest("#wk-member-close")) {
      document.getElementById("wk-member-card").classList.add("hidden");
      BOARD.member = null;
    }
  });

  // A table row that acts like a button has to answer the keyboard like one.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest?.(".wk-row[data-member]");
    if (!row) return;
    e.preventDefault();
    loadMember(row.dataset.member);
  });

  document.addEventListener("change", (e) => {
    if (!e.target.closest("#wk-f-status, #wk-f-period, #wk-f-start, #wk-f-end")) return;
    document.getElementById("wk-f-range")
      .classList.toggle("hidden", document.getElementById("wk-f-period").value !== "custom");
    if (BOARD.member) loadMember(BOARD.member);
  });

  async function load() {
    try {
      const data = await apiFetch(`/api/work/communities/${COMMUNITY}/overview`);
      paintMetrics(data);
      paintPerformance(data.performance);
    } catch (err) {
      document.getElementById("wk-metrics").innerHTML =
        `<div class="wk-empty">${esc(err.message)}</div>`;
      document.getElementById("wk-performance").innerHTML = "";
    }
  }

  registerRefresh(load);
  load();
})();
