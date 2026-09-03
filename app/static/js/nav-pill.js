/* ==========================================================================
   The travelling navigation indicator

   Ported from watermelon.sh's fluid-tabs, whose whole point is that the active
   marker slides between tabs instead of blinking from one to the next.

   The source does that with framer-motion's layoutId, which works because its
   tabs live in one React tree and only the state changes. This application
   navigates by loading a new document, so the old marker and the new one never
   exist at the same time and there is nothing to interpolate between -- the
   slide would simply never be seen.

   So the previous destination is remembered. On arrival the pill is placed
   where it was on the page just left, then moved to the current item on the
   next frame. The travel happens across the navigation, which is exactly where
   the eye is looking for continuity, and it is the same one spring either way.

   Everything here is progressive: with the script absent or an unrecognised
   destination the pill is simply removed and the CSS active state that was
   always there does the marking.
   ========================================================================== */
(function () {
  "use strict";

  var SPRING_MS = 460;

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  /* Coordinates for an absolutely-positioned child are measured from the
     container's padding box and scroll with its content, so the border and the
     scroll offset both have to be taken out of the viewport rects. */
  function measure(container, el, padX, padY) {
    var cr = container.getBoundingClientRect();
    var r = el.getBoundingClientRect();
    return {
      x: r.left - cr.left - container.clientLeft + container.scrollLeft + padX,
      y: r.top - cr.top - container.clientTop + container.scrollTop + padY,
      w: r.width - padX * 2,
      h: r.height - padY * 2
    };
  }

  function apply(pill, m) {
    pill.style.setProperty("--pill-x", m.x + "px");
    pill.style.setProperty("--pill-y", m.y + "px");
    pill.style.setProperty("--pill-w", m.w + "px");
    pill.style.setProperty("--pill-h", m.h + "px");
  }

  function identify(el) {
    return el.getAttribute("href") || el.id || "";
  }

  function mount(container, itemSel, storeKey, padX, padY) {
    if (!container) return;
    var pill = container.querySelector(".nav-pill");
    if (!pill) return;

    var items = Array.prototype.slice.call(container.querySelectorAll(itemSel));
    var active = container.querySelector(itemSel + ".active");

    /* Not every page is a nav destination. Rather than park the pill somewhere
       arbitrary, take it out and let the plain active styling stand. */
    if (!active || !items.length) {
      pill.parentNode.removeChild(pill);
      return;
    }

    container.classList.add("has-pill");

    var here = identify(active);
    var previous = null;
    try {
      previous = sessionStorage.getItem(storeKey);
      sessionStorage.setItem(storeKey, here);
    } catch (_) {
      /* Private mode and blocked storage: no memory, so no travel. */
    }

    var from = null;
    if (previous && previous !== here && !reducedMotion()) {
      for (var i = 0; i < items.length; i++) {
        if (identify(items[i]) === previous) { from = items[i]; break; }
      }
    }

    var travelling = false;

    function settle() { apply(pill, measure(container, active, padX, padY)); }

    /* Re-measure without animating. Fonts finish loading a beat after the page
       does, which is squarely inside the travel: killing the transition to
       correct the geometry would stop the pill dead halfway. While it is in
       flight the new measurement is fed to the running animation instead, so
       it simply retargets. */
    function jump() {
      if (travelling) { settle(); return; }
      pill.classList.add("is-instant");
      settle();
      requestAnimationFrame(function () { pill.classList.remove("is-instant"); });
    }

    if (from) {
      pill.classList.add("is-instant");
      apply(pill, measure(container, from, padX, padY));
      pill.classList.add("is-ready");
      /* Two frames: one for the start position to be committed, one for the
         transition to have something to animate from. A single frame lands the
         pill at the destination with no travel at all. */
      travelling = true;
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          pill.classList.remove("is-instant");
          settle();
          active.classList.add("is-arriving");
          setTimeout(function () {
            active.classList.remove("is-arriving");
            travelling = false;
          }, SPRING_MS);
        });
      });
    } else {
      pill.classList.add("is-instant");
      settle();
      requestAnimationFrame(function () {
        pill.classList.add("is-ready");
        requestAnimationFrame(function () { pill.classList.remove("is-instant"); });
      });
    }

    /* The item can change size after the pill is placed -- a badge arriving,
       a font swapping in, the window resizing. Re-measure without animating,
       because none of those are a navigation. */
    window.addEventListener("resize", jump);
    if (window.ResizeObserver) {
      var first = true;
      new ResizeObserver(function () {
        if (first) { first = false; return; }
        jump();
      }).observe(container);
    }
    if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
      document.fonts.ready.then(jump).catch(function () {});
    }
  }

  /* A strip that switches in place rather than by navigating. No memory is
     needed: both states are in this document, so the pill simply follows
     whichever item currently carries .active.

     It watches the class rather than the click, so it stays correct when the
     selection is changed by something other than a press -- restoring a saved
     view, or a keyboard shortcut -- without this file having to know how any
     of that works. */
  function track(container, itemSel, padX, padY) {
    if (!container) return;
    var pill = container.querySelector(".nav-pill");
    if (!pill) return;

    function current() { return container.querySelector(itemSel + ".active"); }

    /* The selection may not be marked yet -- the page script that owns this
       strip can set it after this runs. So the observer goes on first and the
       pill stays invisible until there is something to sit under, rather than
       this depending on which script happens to run first. */
    var placed = false;

    function place() {
      var now = current();
      if (!now) return;
      if (!placed) {
        placed = true;
        container.classList.add("has-pill");
        pill.classList.add("is-instant");
        apply(pill, measure(container, now, padX, padY));
        pill.classList.add("is-ready");
        requestAnimationFrame(function () { pill.classList.remove("is-instant"); });
        return;
      }
      /* Only geometry is written back, never a class, so this cannot
         retrigger itself. */
      apply(pill, measure(container, now, padX, padY));
    }

    new MutationObserver(place).observe(container, {
      subtree: true, attributes: true, attributeFilter: ["class"]
    });
    place();

    window.addEventListener("resize", function () {
      var now = current();
      if (!now || !placed) return;
      pill.classList.add("is-instant");
      apply(pill, measure(container, now, padX, padY));
      requestAnimationFrame(function () { pill.classList.remove("is-instant"); });
    });
  }

  function init() {
    mount(document.getElementById("sidebar"), ".nav-link", "navPill:sidebar", 0, 0);
    /* The tab bar's items run edge to edge, so the pill is inset to sit as a
       pill on a track rather than as a full-height block. */
    mount(document.querySelector(".bottom-nav"), ".bn-item", "navPill:bottom", 5, 6);
    track(document.querySelector(".calendar-view-switch"), ".btn", 0, 0);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
