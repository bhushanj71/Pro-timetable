/* Prompts for notification permission on whichever device is being used.

   Push subscriptions are per-device: enabling on a laptop does nothing for a
   phone. This banner therefore checks the *current* device every time, and
   the dismissal is remembered per-device rather than on the account. */

(() => {
  const banner = document.getElementById("push-banner");
  if (!banner) return;

  const DISMISS_KEY = "profschedule-push-banner-dismissed";

  const dismissed = () => {
    try {
      return localStorage.getItem(DISMISS_KEY) === "1";
    } catch (_) {
      return false;
    }
  };

  async function evaluate() {
    if (dismissed()) return;

    let status;
    try {
      status = await apiFetch("/api/onboarding/status");
    } catch (_) {
      return; // not signed in
    }

    // Nothing to offer if the server can't send push at all.
    if (!status.push.server_ready || !status.push.enabled) return;

    if (typeof pushSupported === "function" && !pushSupported()) return;

    // iOS refuses the Push API until the app is installed, so point there
    // instead of offering a button that cannot succeed.
    if (typeof iosNeedsInstall === "function" && iosNeedsInstall()) {
      document.getElementById("pb-title").textContent = "Add ProfSchedule to your Home Screen";
      document.getElementById("pb-sub").textContent =
        "Tap Share, then Add to Home Screen, and open it from there to enable notifications.";
      document.getElementById("pb-enable").classList.add("hidden");
      banner.classList.remove("hidden");
      return;
    }

    if (Notification.permission === "denied") {
      document.getElementById("pb-title").textContent = "Notifications are blocked";
      document.getElementById("pb-sub").textContent =
        "Allow notifications for this site in your browser settings to get class reminders.";
      document.getElementById("pb-enable").classList.add("hidden");
      banner.classList.remove("hidden");
      return;
    }

    // Already subscribed on this device?
    const reg = await navigator.serviceWorker?.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    if (sub && Notification.permission === "granted") return;

    banner.classList.remove("hidden");
  }

  document.getElementById("pb-enable")?.addEventListener("click", async (e) => {
    setButtonLoading(e.currentTarget, true);
    try {
      if (await enablePush()) {
        banner.classList.add("hidden");
        // Immediate proof it works, rather than waiting for a real reminder.
        await apiFetch("/api/notifications/test", { method: "POST" });
      }
    } catch (err) {
      // enablePush already toasts a specific reason for known failures; this
      // only covers genuinely unexpected ones.
      console.warn("Enable push failed:", err);
      showToast(err.message || "Could not enable notifications on this device", "error");
    } finally {
      setButtonLoading(e.currentTarget, false);
    }
  });

  document.getElementById("pb-dismiss")?.addEventListener("click", () => {
    banner.classList.add("hidden");
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch (_) {}
  });

  evaluate();
})();
