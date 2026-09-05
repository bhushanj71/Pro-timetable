/* ==========================================================================
   What is behind one of the dashboard's counts

   The three tiles print a number. This is the set the number was drawn from,
   with the dates each assignment has been carrying all along: when it
   arrived, when it was answered, when it was finished.

   The bucket comes from the page rather than from the URL directly, because
   the server has already refused anything that is not one of the three -- so
   by the time this runs there is nothing left to validate.
   ========================================================================== */
(function () {
  "use strict";

  var page = document.getElementById("wk-history");
  if (!page) return;

  var bucket = page.dataset.bucket;
  var list = document.getElementById("wk-history-list");
  var sub = document.getElementById("wk-history-sub");

  /* A date is worth more than an interval here -- this is a record, and "3
     days ago" stops being an answer the moment somebody is looking for what
     happened on the Tuesday. The time comes along for same-day events, which
     on a busy day is most of them. */
  function stamp(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var sameYear = d.getFullYear() === new Date().getFullYear();
    return d.toLocaleDateString(undefined, {
      day: "numeric", month: "short",
      year: sameYear ? undefined : "numeric"
    }) + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  /* Only the steps that actually happened. An empty row saying "Answered: --"
     is noise; a missing one already says it. */
  function trail(item) {
    var steps = [
      ["Assigned", item.assigned_at],
      ["Answered", item.responded_at],
      ["Completed", item.completed_at]
    ].filter(function (s) { return s[1]; });

    return steps.map(function (s) {
      return '<span class="wk-hstep"><b>' + s[0] + "</b> " + esc(stamp(s[1])) + "</span>";
    }).join("");
  }

  function row(item) {
    var due = item.due_date
      ? '<span class="wk-hdue' + (item.overdue ? " is-late" : "") + '">' +
        (item.overdue ? "Was due " : "Due ") + esc(stamp(item.due_date)) + "</span>"
      : "";

    return '' +
      '<a class="wk-hrow" href="/work/task/' + encodeURIComponent(item.task_id) + '">' +
        '<div class="wk-hrow-main">' +
          '<div class="wk-hrow-title">' + esc(item.title) + "</div>" +
          '<div class="wk-hrow-meta">' +
            esc(item.community.icon || "") + " " + esc(item.community.name) +
            " · assigned by " + esc(item.assigned_by.name) +
          "</div>" +
          '<div class="wk-htrail">' + trail(item) + due + "</div>" +
        "</div>" +
        '<div class="wk-hrow-right">' +
          '<span class="wk-hpct">' + (item.progress || 0) + "%</span>" +
          '<span class="wk-chev" aria-hidden="true">›</span>' +
        "</div>" +
      "</a>";
  }

  function empty() {
    return {
      active: "Nothing is active right now.",
      pending: "Nothing is waiting on your answer.",
      completed: "Nothing finished yet."
    }[bucket] || "Nothing here.";
  }

  (async function load() {
    try {
      var d = await apiFetch("/api/work/history/" + encodeURIComponent(bucket));
      sub.textContent = d.count === 1 ? "1 item" : d.count + " items";
      list.innerHTML = d.items.length
        ? d.items.map(row).join("")
        : '<div class="wk-empty">' + empty() + "</div>";
    } catch (err) {
      sub.textContent = "";
      list.innerHTML = '<div class="wk-empty">' + esc(err.message || "Could not load this.") + "</div>";
    }
  })();

  document.getElementById("wk-history-back")?.addEventListener("click", function () {
    /* Back to where they came from when that was this application, and to the
       Work home when it was not -- a fresh tab on a linked URL has nothing
       behind it, and a dead button is worse than a predictable one. */
    if (history.length > 1 && document.referrer.indexOf(location.host) !== -1) history.back();
    else location.href = "/work";
  });
})();
