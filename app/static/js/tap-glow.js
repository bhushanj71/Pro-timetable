/* ==========================================================================
   Press feedback

   One listener for the whole application, and two answers to a press: the
   edge of the page lights and turns once, and the control that was pressed
   pops. They share this file because they share the target list below -- the
   moment "every button in the application" is maintained as two lists, it is
   one list and a stale one.

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

  /* The pop on the control itself. Comfortably past its 240ms animation; the
     class is harmless once that has finished, because the animation does not
     fill and the element goes back to its own scale on its own. */
  var PRESS_CLASS = "is-pressed";
  var PRESS_MS = 600;
  var pressTimers = new WeakMap();

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

  /* Unlike the glow, this leaves no element behind -- only a class on
     something that was already on the page -- so it is cleaned up on a timer
     alone. animationend would be tidier by a few hundred milliseconds and is
     the reason the glow listens for it: a full-screen overlay left lit in a
     backgrounded tab is a fault, and a class that no longer paints anything
     is not. */
  function zoom(el) {
    var pending = pressTimers.get(el);
    if (pending) clearTimeout(pending);

    /* A second press while the first is still playing is ignored unless the
       animation is taken off and put back: same name on the same element, so
       the engine goes on running the one it already started. Reading
       offsetWidth in between is what forces it to restart. */
    el.classList.remove(PRESS_CLASS);
    void el.offsetWidth;
    el.classList.add(PRESS_CLASS);

    pressTimers.set(el, setTimeout(function () {
      pressTimers.delete(el);
      el.classList.remove(PRESS_CLASS);
    }, PRESS_MS));
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest(TARGETS);
    if (!el) return;
    /* A refused press should not be congratulated. */
    if (el.disabled || el.getAttribute("aria-disabled") === "true") return;
    glow();
    zoom(el);
  }, true);

  window.addEventListener("pagehide", clear);
})();
