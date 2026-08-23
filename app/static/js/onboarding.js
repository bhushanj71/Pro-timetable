/* First-login notification setup.
   Shown once, right after signing in, so reminders get configured without the
   professor having to discover the settings page. Each step reflects live
   state and disappears once satisfied. */

(() => {
  const modal = document.getElementById("onboarding-modal");
  if (!modal) return;

  let status = null;

  const el = (id) => document.getElementById(id);
  const markDone = (stepId, stateId, btnId, label) => {
    el(stepId)?.classList.add("done");
    const state = el(stateId);
    if (state) {
      state.textContent = label || "✓ On";
      state.classList.remove("hidden");
    }
    el(btnId)?.classList.add("hidden");
  };

  async function loadStatus() {
    status = await apiFetch("/api/onboarding/status");
    return status;
  }

  function renderEmail() {
    const sub = el("onb-email-sub");
    if (status.email.server_ready) return; // default markup already says "On"
    // Be honest rather than promising delivery the server can't do yet.
    el("onb-email")?.classList.remove("done");
    if (sub) sub.textContent = `${status.email.address} — email delivery isn't set up on the server yet`;
    const state = el("onb-email-state");
    if (state) { state.textContent = "Unavailable"; state.style.color = "var(--text-muted)"; }
  }

  async function renderPush() {
    const btn = el("onb-push-btn");
    const sub = el("onb-push-sub");

    if (!status.push.server_ready) {
      if (sub) sub.textContent = "Push isn't configured on the server yet";
      btn?.classList.add("hidden");
      return;
    }
    if (typeof pushSupported === "function" && !pushSupported()) {
      if (sub) sub.textContent = "This browser doesn't support push notifications";
      btn?.classList.add("hidden");
      return;
    }
    if (typeof iosNeedsInstall === "function" && iosNeedsInstall()) {
      if (sub) sub.textContent = "On iPhone: tap Share → Add to Home Screen, then reopen from that icon";
      btn?.classList.add("hidden");
      return;
    }

    // Already subscribed on this device?
    const reg = await navigator.serviceWorker?.getRegistration();
    const sub_ = await reg?.pushManager.getSubscription();
    if (sub_ && Notification.permission === "granted") {
      markDone("onb-push", "onb-push-state", "onb-push-btn");
    } else if (Notification.permission === "denied") {
      if (sub) sub.textContent = "Blocked — allow notifications for this site in your browser settings";
      btn?.classList.add("hidden");
    }
  }

  function renderCalendar() {
    const btn = el("onb-cal-btn");
    if (status.google.connected) {
      markDone("onb-cal", "onb-cal-state", "onb-cal-btn", "✓ Google");
      return;
    }
    // Without Google OAuth configured there's nothing to "connect" to, so go
    // straight to the subscription feed rather than showing a dead button.
    if (!status.google.available) {
      btn.textContent = "Set up";
    }
  }

  el("onb-push-btn")?.addEventListener("click", async (e) => {
    setButtonLoading(e.currentTarget, true);
    try {
      if (await enablePush()) markDone("onb-push", "onb-push-state", "onb-push-btn");
    } catch (err) {
      showToast(err.message || "Could not enable notifications", "error");
    } finally {
      setButtonLoading(e.currentTarget, false);
    }
  });

  el("onb-cal-btn")?.addEventListener("click", () => {
    const options = el("onb-cal-options");
    options.classList.remove("hidden");
    el("onb-google-opt")?.classList.toggle("hidden", !status.google.available);
    const input = el("onb-feed-url");
    if (input) input.value = status.calendar_feed_url;
    options.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  el("onb-copy-feed")?.addEventListener("click", async () => {
    const input = el("onb-feed-url");
    try {
      await navigator.clipboard.writeText(input.value);
      showToast("Calendar link copied", "success");
    } catch (_) {
      input.select();
      showToast("Press Ctrl/Cmd+C to copy", "info");
    }
  });

  async function finish() {
    modal.classList.add("hidden");
    try {
      await apiFetch("/api/onboarding/complete", { method: "POST" });
    } catch (_) {
      /* dismissing must never block the UI */
    }
  }

  el("onb-done")?.addEventListener("click", finish);
  el("onb-skip")?.addEventListener("click", finish);
  // Clicking the backdrop counts as dismissing.
  modal.addEventListener("click", (e) => { if (e.target === modal) finish(); });

  (async () => {
    try {
      await loadStatus();
    } catch (_) {
      return; // not signed in, or the API is unavailable
    }
    if (!status.needs_setup) return;

    renderEmail();
    await renderPush();
    renderCalendar();

    // Let the page settle first so the modal doesn't fight the entry animation.
    setTimeout(() => modal.classList.remove("hidden"), 700);
  })();
})();
