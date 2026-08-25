/* First-login setup: install the app, then turn on notifications.

   Reminders are delivered by Web Push from the installed app, so these two
   steps are the whole configuration — no external calendar involved. */

(() => {
  const modal = document.getElementById("onboarding-modal");
  if (!modal) return;

  const el = (id) => document.getElementById(id);
  let status = null;

  const markDone = (stepId, stateId, btnId, label) => {
    el(stepId)?.classList.add("done");
    const state = el(stateId);
    if (state) {
      state.textContent = label || "✓ Done";
      state.classList.remove("hidden");
    }
    el(btnId)?.classList.add("hidden");
  };

  /* ---------------- Step 1: install ---------------- */
  function renderInstall() {
    const sub = el("onb-install-sub");
    const btn = el("onb-install-btn");
    const iosHelp = el("onb-ios-help");

    if (isStandalone()) {
      if (sub) sub.textContent = "Running from your home screen";
      markDone("onb-install", "onb-install-state", "onb-install-btn");
      iosHelp?.classList.add("hidden");
      return;
    }

    if (isIOSDevice()) {
      if (sub) sub.textContent = "Required on iPhone before notifications can work";
      btn?.classList.add("hidden");
      iosHelp?.classList.remove("hidden");
      return;
    }

    if (deferredInstallPrompt) {
      if (sub) sub.textContent = "Keeps ProfSchedule one tap away";
      btn?.classList.remove("hidden");
    } else {
      // Desktop Chrome, or a browser with no install support.
      if (sub) sub.textContent = "Optional here — notifications work in this browser too";
      btn?.classList.add("hidden");
    }
  }

  // Chrome may fire beforeinstallprompt after this script runs.
  document.addEventListener("pwa-installable", renderInstall);
  document.addEventListener("pwa-installed", renderInstall);

  el("onb-install-btn")?.addEventListener("click", async (e) => {
    setButtonLoading(e.currentTarget, true);
    try {
      const result = await promptInstall();
      if (result === "ios") el("onb-ios-help")?.classList.remove("hidden");
      if (result === "installed" || result === "prompted") renderInstall();
    } finally {
      setButtonLoading(e.currentTarget, false);
    }
  });

  /* ---------------- Step 2: notifications ---------------- */
  async function renderPush() {
    const sub = el("onb-push-sub");
    const btn = el("onb-push-btn");

    if (!status.push.server_ready) {
      if (sub) sub.textContent = "Push isn't configured on the server yet";
      btn?.classList.add("hidden");
      return;
    }
    if (!pushSupported()) {
      if (sub) sub.textContent = "This browser doesn't support notifications";
      btn?.classList.add("hidden");
      return;
    }
    // iOS refuses the Push API until the app is installed, so don't offer a
    // button that is guaranteed to fail.
    if (isIOSDevice() && !isStandalone()) {
      if (sub) sub.textContent = "Add to your home screen first (step 1), then reopen and allow";
      btn?.classList.add("hidden");
      return;
    }

    const reg = await navigator.serviceWorker?.getRegistration();
    const existing = await reg?.pushManager.getSubscription();
    if (existing && Notification.permission === "granted") {
      markDone("onb-push", "onb-push-state", "onb-push-btn", "✓ On");
    } else if (Notification.permission === "denied") {
      if (sub) sub.textContent = "Blocked — allow notifications for this site in your browser settings";
      btn?.classList.add("hidden");
    }
  }

  el("onb-push-btn")?.addEventListener("click", async (e) => {
    setButtonLoading(e.currentTarget, true);
    try {
      if (await enablePush()) markDone("onb-push", "onb-push-state", "onb-push-btn", "✓ On");
    } catch (err) {
      showToast(err.message || "Could not enable notifications", "error");
    } finally {
      setButtonLoading(e.currentTarget, false);
    }
  });

  /* ---------------- Email ---------------- */
  function renderEmail() {
    if (status.email.server_ready) return;
    el("onb-email")?.classList.remove("done");
    const sub = el("onb-email-sub");
    if (sub) sub.textContent = `${status.email.address} — not set up on the server yet`;
    const state = el("onb-email-state");
    if (state) {
      state.textContent = "Unavailable";
      state.style.color = "var(--text-muted)";
    }
  }

  /* ---------------- Dismiss ---------------- */
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
  modal.addEventListener("click", (e) => { if (e.target === modal) finish(); });

  (async () => {
    try {
      status = await cachedFetch("/api/onboarding/status");
    } catch (_) {
      return;
    }
    if (status.completed || status.push.devices > 0) return;

    renderInstall();
    await renderPush();
    renderEmail();

    // Let the page settle so the modal doesn't fight the entry animation.
    setTimeout(() => modal.classList.remove("hidden"), 700);
  })();
})();
