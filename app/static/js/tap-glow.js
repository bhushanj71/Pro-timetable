/* ==========================================================================
   Tap glow

   One listener for the whole application: press anything that behaves like a
   control and a gradient ring turns once around it and fades.

   Delegated from the document in the capture phase, so it does not matter how
   many components stop propagation on their own buttons -- and nothing has to
   be wired up per control, which is the only way "every button in the app"
   stays true as pages are added.

   The ring is its own element over the control rather than a pseudo-element
   on it. Styling ::after on the target would mean giving every control
   position:relative, and that quietly re-parents any absolutely-positioned
   child it has -- the tab bar's badges, the navigating spinner in the
   sidebar. This touches nothing that already exists.
   ========================================================================== */
(function () {
  "use strict";

  /* Things that answer a press. Anything that only looks like text is left
     out: a ring around a paragraph reads as a fault, not as feedback. */
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

  var RING = 3;          // must match the padding in .tap-glow
  var SAFETY_MS = 1200;  // longer than the animation, shorter than a nuisance

  /* One at a time. Holding the pointer down on a repeat-clicked control would
     otherwise stack rings that all fade on their own schedule. */
  var live = null;

  function clear() {
    if (!live) return;
    var node = live;
    live = null;
    if (node.parentNode) node.parentNode.removeChild(node);
  }

  /* The ring sits three pixels outside the control, so its corners are three
     pixels rounder. Only worth adjusting when the radius is a single length;
     anything shaped (a card with two square corners) is copied as it stands. */
  function ringRadius(radius) {
    var single = /^(\d+(?:\.\d+)?)px$/.exec(radius || "");
    if (single) return (parseFloat(single[1]) + RING) + "px";
    return radius && radius !== "0px" ? radius : "10px";
  }

  function glow(el) {
    var r = el.getBoundingClientRect();
    /* Nothing to draw around: a control that is hidden, collapsed, or has
       just been removed by the very click being handled. */
    if (!r.width || !r.height) return;

    clear();

    var ring = document.createElement("span");
    ring.className = "tap-glow";
    ring.setAttribute("aria-hidden", "true");
    ring.style.left = (r.left - RING) + "px";
    ring.style.top = (r.top - RING) + "px";
    ring.style.width = (r.width + RING * 2) + "px";
    ring.style.height = (r.height + RING * 2) + "px";
    ring.style.borderRadius = ringRadius(getComputedStyle(el).borderRadius);

    document.body.appendChild(ring);
    live = ring;

    ring.addEventListener("animationend", function () {
      if (live === ring) clear();
    }, { once: true });

    /* animationend never arrives in a backgrounded tab, and a ring left over
       the page is worse than no ring at all. */
    setTimeout(function () {
      if (live === ring) clear();
    }, SAFETY_MS);
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest(TARGETS);
    if (!el) return;
    /* A refused press should not be congratulated. */
    if (el.disabled || el.getAttribute("aria-disabled") === "true") return;
    glow(el);
  }, true);

  /* A ring is anchored to viewport coordinates, so once the page moves it is
     no longer around anything. Cheaper to drop it than to chase the control. */
  window.addEventListener("scroll", clear, { passive: true, capture: true });
  window.addEventListener("resize", clear);
  window.addEventListener("pagehide", clear);
})();
