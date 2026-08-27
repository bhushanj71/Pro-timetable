/* Native behaviour, when the page is running inside the mobile shell.

   The app is one server-rendered site whether it is opened in a browser or
   in the Capacitor app. This file is the difference between the two: it does
   nothing at all in a browser, and everywhere it does act, it is because the
   platform expects something a web page cannot do on its own.

   Nothing here is decoration. Each block exists because leaving it out
   produces a specific, visible defect on a phone: content under the notch, a
   back gesture that quits the app mid-form, a tap on a notification that
   opens the dashboard instead of the task it was about. */
(function () {
  const Cap = window.Capacitor;
  const isNative = !!(Cap && Cap.isNativePlatform && Cap.isNativePlatform());

  // The web build must be completely unaffected.
  if (!isNative) return;

  document.documentElement.classList.add("is-native", `is-${Cap.getPlatform()}`);
  const plugin = (name) => (Cap.Plugins && Cap.Plugins[name]) || null;

  /* ---------- Safe areas ----------
     iOS reports the notch and home indicator only to native code. The CSS
     env() values cover the viewport, but the fixed top bar and tab bar need
     the numbers as custom properties to pad themselves correctly. */
  const applyInsets = () => {
    const root = document.documentElement.style;
    // env() is authoritative where it works; these are the fallback the
    // layout reads so a value always exists.
    root.setProperty("--safe-top", "env(safe-area-inset-top, 0px)");
    root.setProperty("--safe-bottom", "env(safe-area-inset-bottom, 0px)");
  };
  applyInsets();

  /* ---------- Status bar ----------
     Follows the app's own light/dark choice. Left alone, a dark theme gets
     black text on a black bar and the clock disappears. */
  const StatusBar = plugin("StatusBar");
  if (StatusBar) {
    const paint = () => {
      const dark = document.documentElement.dataset.theme === "dark" ||
        (!document.documentElement.dataset.theme &&
          window.matchMedia("(prefers-color-scheme: dark)").matches);
      StatusBar.setStyle({ style: dark ? "DARK" : "LIGHT" }).catch(() => {});
      if (Cap.getPlatform() === "android") {
        StatusBar.setBackgroundColor({ color: dark ? "#171311" : "#FDF9F7" }).catch(() => {});
      }
    };
    paint();
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paint);
    // theme.js flips this attribute; watching it keeps the bar in step.
    new MutationObserver(paint).observe(document.documentElement, {
      attributes: true, attributeFilter: ["data-theme"],
    });
  }

  /* ---------- Splash ----------
     Held until the page is actually painted rather than hidden on a timer:
     a splash that leaves early shows a white flash, and one on a fixed delay
     wastes the time it guessed wrong by. */
  const SplashScreen = plugin("SplashScreen");
  if (SplashScreen) {
    const dismiss = () => SplashScreen.hide({ fadeOutDuration: 200 }).catch(() => {});
    if (document.readyState === "complete") requestAnimationFrame(dismiss);
    else window.addEventListener("load", () => requestAnimationFrame(dismiss), { once: true });
    // Backstop: never leave someone staring at a logo because a load stalled.
    setTimeout(dismiss, 6000);
  }

  /* ---------- Hardware back ----------
     Android's back gesture closes the app by default. On a page with an open
     sheet that is the wrong answer, and on any inner page it loses the whole
     session's navigation. */
  const App = plugin("App");
  if (App) {
    App.addListener("backButton", ({ canGoBack }) => {
      // Anything open is dismissed first, innermost thing last opened.
      const openModal = [...document.querySelectorAll(".modal-backdrop:not(.hidden)")]
        .filter((m) => m.dataset.mandatory !== "true").pop();
      if (openModal) { openModal.classList.add("hidden"); return; }

      const panel = document.getElementById("notif-panel");
      if (panel && !panel.classList.contains("hidden")) { panel.classList.add("hidden"); return; }

      const menu = document.getElementById("user-menu");
      if (menu && !menu.classList.contains("hidden")) { menu.classList.add("hidden"); return; }

      if (canGoBack) { window.history.back(); return; }

      // At the root with nothing open. Quitting is right, but not by
      // surprise: a single stray back should not end the session.
      if (window.__backArmed) { App.exitApp(); return; }
      window.__backArmed = true;
      if (typeof showToast === "function") showToast("Press back again to exit", "success");
      setTimeout(() => { window.__backArmed = false; }, 2000);
    });

    /* ---------- Deep links ----------
       profschedule://work/task/<id> from a push payload, and the https URLs
       verified by assetlinks.json. Both arrive here. */
    App.addListener("appUrlOpen", ({ url }) => {
      if (!url) return;
      let path = null;
      try {
        if (url.startsWith("profschedule://")) {
          path = "/" + url.slice("profschedule://".length).replace(/^\/+/, "");
        } else {
          const parsed = new URL(url);
          path = parsed.pathname + parsed.search;
        }
      } catch (_) { return; }

      // Mapped rather than navigated to blindly: a link is untrusted input,
      // and the app should not follow it to an arbitrary origin.
      const routes = [
        // These are real pages now, so a deep link lands on the thing itself
        // rather than on the dashboard with a dialog opened over it.
        [/^\/work\/task\/([\w-]+)$/, (m) => `/work/task/${encodeURIComponent(m[1])}`],
        [/^\/work\/community\/([\w-]+)$/, (m) => `/work/community/${encodeURIComponent(m[1])}`],
        [/^\/personal\/lecture\/([\w-]+)$/, (m) => `/timetable?event=${encodeURIComponent(m[1])}`],
        [/^\/(dashboard|work|timetable|calendar|tasks|reminders|profile)\b/, (m) => m[0]],
      ];
      for (const [pattern, build] of routes) {
        const match = path.match(pattern);
        if (match) { window.location.assign(build(match)); return; }
      }
      window.location.assign("/dashboard");
    });

    // Coming back from the background: anything time-sensitive is stale.
    App.addListener("appStateChange", ({ isActive }) => {
      if (!isActive) return;
      if (typeof clearCache === "function") clearCache();
      window.dispatchEvent(new CustomEvent("schedule-updated"));
      window.dispatchEvent(new CustomEvent("work-updated"));
    });
  }

  /* ---------- Offline ----------
     A phone loses signal constantly. Saying so is the difference between
     "the app is broken" and "you are in a lift". */
  const Network = plugin("Network");
  if (Network) {
    let banner = null;
    const show = (online) => {
      if (online) { banner?.remove(); banner = null; return; }
      if (banner) return;
      banner = document.createElement("div");
      banner.className = "native-offline";
      banner.setAttribute("role", "status");
      banner.textContent = "Offline — showing what was last loaded";
      document.body.appendChild(banner);
    };
    Network.getStatus().then((s) => show(s.connected)).catch(() => {});
    Network.addListener("networkStatusChange", (s) => {
      show(s.connected);
      if (s.connected && typeof clearCache === "function") {
        clearCache();
        window.dispatchEvent(new CustomEvent("schedule-updated"));
      }
    });
  }

  /* ---------- Keyboard ----------
     Without this the focused field can sit behind the keyboard on a short
     screen, with no way to see what is being typed. */
  const Keyboard = plugin("Keyboard");
  if (Keyboard) {
    Keyboard.addListener("keyboardWillShow", (info) => {
      document.documentElement.style.setProperty("--keyboard-h", `${info.keyboardHeight}px`);
      document.documentElement.classList.add("keyboard-open");
      setTimeout(() => document.activeElement?.scrollIntoView({ block: "center", behavior: "smooth" }), 60);
    });
    Keyboard.addListener("keyboardWillHide", () => {
      document.documentElement.style.setProperty("--keyboard-h", "0px");
      document.documentElement.classList.remove("keyboard-open");
    });
  }

  /* ---------- Haptics ----------
     Only on the two actions that commit something. Buzzing on every tap is
     noise, and users turn the whole thing off. */
  const Haptics = plugin("Haptics");
  if (Haptics) {
    document.addEventListener("click", (e) => {
      if (e.target.closest("#wk-del-go, [data-task-respond], #wk-save-progress")) {
        Haptics.impact({ style: "MEDIUM" }).catch(() => {});
      }
    });
  }
})();
