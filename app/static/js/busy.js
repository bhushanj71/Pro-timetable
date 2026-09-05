/* ==========================================================================
   "Something is happening"

   Press a control and, if what it started has not finished in a moment, the
   loader appears at the top of the screen.

   The threshold is short, so the hard part is not showing it -- it is not
   showing it for the many presses that finish instantly. Opening a dialog,
   ticking a filter, switching a tab: those are done inside the frame and must
   leave the screen alone. So the timer only produces a pill if there is
   actually something outstanding when it fires, which means knowing what
   "outstanding" is:

     - a request in flight, counted by wrapping the one helper every part of
       this application fetches through, and
     - a navigation begun, which ends when the document is replaced.

   A press that starts neither is a press that finished, and nothing appears.

   The window matters as much as the threshold. A handler usually does a
   little work before it reaches its fetch, so a request that begins slightly
   after the timer has run still belongs to that press -- for a short while
   afterwards, work starting counts as work the press caused.
   ========================================================================== */
(function () {
  "use strict";

  var THRESHOLD_MS = 30;   /* how long a press may take before it is reported */
  var ATTRIBUTION_MS = 600; /* how long work still counts as that press's doing */

  var TARGETS = [
    "button", ".btn", ".icon-btn", ".nav-link", ".bn-item", ".chip-x",
    ".qa-card", ".tab-btn", ".wk-viewall", ".user-chip", ".mode-switch a",
    ".field-toggle", "label", '[role="button"]',
    /* Rows that open a page of their own. */
    ".wk-community", ".wk-task", "a[href]"
  ].join(",");

  var inFlight = 0;
  var navigating = false;
  var watchUntil = 0;
  var timer = null;
  var pill = null;

  function pending() {
    return navigating || inFlight > 0;
  }

  function show() {
    if (pill || !pending()) return;
    /* The route veil says the same thing at greater length. Two of them at
       once is one too many. */
    if (document.getElementById("route-veil")) return;

    pill = document.createElement("div");
    pill.className = "busy-pill";
    pill.innerHTML = typeof loaderMarkup === "function"
      ? loaderMarkup("", "Loading")
      : '<span role="status" aria-live="polite">Loading</span>';
    /* The helper stacks its label under the animation, which is right in a
       veil and wrong in a pill -- it made this as tall as it was wide. The
       class that lays the two out in a row already exists. */
    pill.firstElementChild?.classList.add("loader-inline");
    document.body.appendChild(pill);
  }

  function hide() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (pill && pill.parentNode) pill.parentNode.removeChild(pill);
    pill = null;
  }

  function settle() {
    if (!pending()) hide();
  }

  /* Called when work begins. If it began inside the window a press opened,
     the press is answerable for it: report it once the threshold has passed,
     or straight away if it already has. */
  function armFor(startedAt) {
    if (Date.now() > watchUntil) return;
    var waited = Date.now() - startedAt;
    if (timer) clearTimeout(timer);
    timer = setTimeout(show, Math.max(0, THRESHOLD_MS - waited));
  }

  /* --- what counts as outstanding ------------------------------------- */

  /* Every request in the application goes through this one helper, so
     wrapping it is the whole of the accounting. Declared as a function in
     app.js, which makes it a property here to replace. */
  if (typeof window.apiFetch === "function") {
    var inner = window.apiFetch;
    window.apiFetch = function () {
      var startedAt = Date.now();
      inFlight++;
      armFor(startedAt);
      var done = function (v) { inFlight = Math.max(0, inFlight - 1); settle(); return v; };
      var fail = function (e) { inFlight = Math.max(0, inFlight - 1); settle(); throw e; };
      try {
        return Promise.resolve(inner.apply(this, arguments)).then(done, fail);
      } catch (e) {
        fail(e);
      }
    };
  }

  /* The veil supersedes the pill rather than sitting on top of it. */
  if (typeof window.showRouteVeil === "function") {
    var innerVeil = window.showRouteVeil;
    window.showRouteVeil = function () {
      hide();
      return innerVeil.apply(this, arguments);
    };
  }

  /* --- the press ------------------------------------------------------- */
  function leavesThePage(el, e) {
    var link = el.closest("a[href]");
    if (!link) return false;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || link.target === "_blank") return false;
    var href = link.getAttribute("href") || "";
    if (!href || href[0] === "#" || /^(mailto|tel|javascript):/i.test(href)) return false;
    var url;
    try { url = new URL(href, location.href); } catch (_) { return false; }
    if (url.origin !== location.origin) return false;
    /* Already here: nothing will load, so nothing should be reported. */
    return url.pathname !== location.pathname || url.search !== location.search;
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest(TARGETS);
    if (!el) return;
    if (el.disabled || el.getAttribute("aria-disabled") === "true") return;

    watchUntil = Date.now() + ATTRIBUTION_MS;
    if (leavesThePage(el, e)) navigating = true;

    if (timer) clearTimeout(timer);
    timer = setTimeout(show, THRESHOLD_MS);
  }, true);

  /* A navigation that never happens -- a refused request, a download, a link
     that turned out to be a no-op -- must not leave this running. */
  window.addEventListener("pagehide", hide);
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) { navigating = false; inFlight = 0; hide(); }
  });
})();
