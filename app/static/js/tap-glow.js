/* ==========================================================================
   Tap glow

   One listener for the whole application: press anything that behaves like a
   control and the edge of the page lights and turns once.

   Delegated from the document in the capture phase, so it does not matter how
   many components stop propagation on their own buttons -- and nothing has to
   be wired up per control, which is the only way "every button in the app"
   stays true as pages are added.

   The glow frames the viewport rather than the control that was pressed. That
   is what Skiper86 actually is, and it also means this file has no geometry
   to keep: nothing to measure, nothing to re-measure when the page scrolls or
   the window changes, and no way for the effect to end up somewhere the
   control no longer is.
   ========================================================================== */
(function () {
  "use strict";

  /* Things that answer a press. Anything that only looks like text is left
     out: lighting the screen because somebody selected a paragraph reads as a
     fault, not as feedback. */
  var TARGETS = [
    "button",
    ".btn",
    ".icon-btn",
    ".nav-link",
    ".bn-item",
    ".chip-x",
    ".qa-card",
    ".tab-btn",
    ".wk-viewall",
    ".user-chip",
    ".mode-switch a",
    ".field-toggle",
    "label",
    '[role="button"]'
  ].join(",");

  /* Comfortably past the 1400ms animation. */
  var SAFETY_MS = 2000;

  /* One at a time. A held-down or repeatedly-pressed control would otherwise
     stack frames that each fade on their own schedule, and four of them at
     once is four times the opacity. */
  var live = null;
  var timer = null;

  function clear() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (!live) return;
    var node = live;
    live = null;
    if (node.parentNode) node.parentNode.removeChild(node);
  }

  function glow() {
    clear();

    var frame = document.createElement("div");
    frame.className = "tap-glow";
    frame.setAttribute("aria-hidden", "true");
    document.body.appendChild(frame);
    live = frame;

    frame.addEventListener("animationend", function () {
      if (live === frame) clear();
    }, { once: true });

    /* animationend never arrives in a backgrounded tab, and a lit edge left
       over the page is worse than no glow at all. */
    timer = setTimeout(function () {
      if (live === frame) clear();
    }, SAFETY_MS);
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest(TARGETS);
    if (!el) return;
    /* A refused press should not be congratulated. */
    if (el.disabled || el.getAttribute("aria-disabled") === "true") return;
    glow();
  }, true);

  window.addEventListener("pagehide", clear);
})();
