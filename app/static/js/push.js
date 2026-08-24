/* Web Push enrolment.
   Registers the service worker, asks for notification permission, and hands
   the resulting subscription to the server so reminders can reach the device
   even when the site is closed. */

function urlBase64ToUint8Array(base64String) {
  // VAPID keys are base64url; the Push API wants a Uint8Array.
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

const pushSupported = () =>
  "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

/** iOS only exposes the Push API to installed (home-screen) web apps. */
function iosNeedsInstall() {
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const installed = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
  return isIOS && !installed;
}

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch (err) {
    console.warn("Service worker registration failed:", err);
    return null;
  }
}

async function enablePush() {
  if (!pushSupported()) {
    showToast("This browser doesn't support push notifications.", "error");
    return false;
  }
  if (iosNeedsInstall()) {
    showToast("On iPhone, first tap Share → Add to Home Screen, then open the app from there.", "error");
    return false;
  }

  const { public_key: publicKey, enabled } = await apiFetch("/api/push/public-key");
  if (!enabled || !publicKey) {
    showToast("Push isn't configured on the server yet.", "error");
    return false;
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    showToast(
      permission === "denied"
        ? "Notifications are blocked. Enable them for this site in your browser settings."
        : "Notification permission was dismissed.",
      "error"
    );
    return false;
  }

  const reg = await registerServiceWorker();
  if (!reg) {
    showToast("Could not start the notification service worker.", "error");
    return false;
  }
  await navigator.serviceWorker.ready;

  // Reuse an existing subscription if the browser already has one.
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    try {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    } catch (err) {
      // Chrome reports several unrelated conditions as the same opaque
      // "Registration failed - push service error", so translate it.
      const detail = `${esc(err.name)}: ${esc(err.message)}`;
      let advice;

      if (/push service error|AbortError/i.test(detail)) {
        advice =
          "Your browser could not reach its push service. This is usually a network " +
          "or browser-profile problem rather than the app: try a different network, " +
          "or test on your phone instead of this laptop.";
      } else if (/permission/i.test(detail)) {
        advice = "Notification permission was refused for this site.";
      } else if (/applicationServerKey|InvalidAccessError|InvalidStateError/i.test(detail)) {
        // A key change invalidates existing subscriptions; clearing lets the
        // next attempt succeed.
        advice = "This device holds a subscription from a different key. Clearing it — please try again.";
        const stale = await reg.pushManager.getSubscription();
        if (stale) await stale.unsubscribe();
      } else {
        advice = "Could not subscribe this device.";
      }

      console.warn("Push subscribe failed:", detail);
      showToast(`${advice} (${esc(err.name)})`, "error");
      return false;
    }
  }

  const json = sub.toJSON();
  await apiFetch("/api/push/subscribe", {
    method: "POST",
    body: { endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth } },
  });

  showToast("✓ Push notifications enabled on this device", "success");
  return true;
}

async function disablePush() {
  if (!("serviceWorker" in navigator)) return;
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await apiFetch("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } });
    await sub.unsubscribe();
  }
  showToast("Push notifications disabled on this device", "success");
}

/** List the devices that will actually receive a push.

    Push subscriptions live in the browser, not the account: tapping Enable on
    a laptop does nothing for a phone, and a failed subscribe leaves no trace.
    Showing what is registered turns "why no notifications?" into something
    the professor can see and fix. */
async function refreshDeviceList() {
  const box = document.getElementById("device-list");
  if (!box) return;

  let devices;
  try {
    ({ devices } = await apiFetch("/api/push/devices"));
  } catch (_) {
    box.innerHTML = `<div class="device-empty">Could not load your devices.</div>`;
    return;
  }

  // Identify the row belonging to the browser we are looking at right now.
  let myTail = null;
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    if (sub) myTail = sub.endpoint.slice(-12);
  } catch (_) {}

  if (!devices.length) {
    box.innerHTML = `<div class="device-empty warn">
        <strong>No devices registered yet.</strong><br>
        Reminders can only reach a phone that has tapped Enable <em>on that phone</em>.
        Open this page on your phone and turn notifications on there.
      </div>`;
    return;
  }

  box.innerHTML = devices
    .map((d) => {
      const mine = myTail && d.endpoint_tail === myTail;
      return `<div class="device-row">
        <span class="dv-ico">📱</span>
        <div class="dv-main">
          <div class="dv-name">${esc(d.label)}${mine ? ` <span class="dv-this">· this device</span>` : ""}</div>
          <div class="dv-when">Added ${fmtDate(d.added)}</div>
        </div>
        <button type="button" class="dv-forget" data-device="${d.id}">Forget</button>
      </div>`;
    })
    .join("");
}

// Delegated: refreshDeviceList replaces these rows wholesale.
document.getElementById("device-list")?.addEventListener("click", async (e) => {
  const btn = e.target.closest(".dv-forget");
  if (!btn) return;
  setButtonLoading(btn, true);
  try {
    await apiFetch(`/api/push/devices/${btn.dataset.device}`, { method: "DELETE" });
    await refreshDeviceList();
    refreshPushStatus();
  } catch (_) {
    showToast("Could not remove that device", "error");
    setButtonLoading(btn, false);
  }
});

/** Reflect the current state in the profile page controls. */
async function refreshPushStatus() {
  const statusEl = document.getElementById("push-status");
  const enableBtn = document.getElementById("push-enable-btn");
  const disableBtn = document.getElementById("push-disable-btn");
  if (!statusEl) return;

  if (!pushSupported()) {
    statusEl.textContent = "Not supported by this browser.";
    enableBtn?.setAttribute("disabled", "true");
    return;
  }
  if (iosNeedsInstall()) {
    statusEl.textContent = "On iPhone: tap Share → Add to Home Screen, open the app from there, then enable.";
    return;
  }

  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  const granted = Notification.permission === "granted";

  if (sub && granted) {
    statusEl.textContent = "✓ Enabled on this device.";
    enableBtn?.classList.add("hidden");
    disableBtn?.classList.remove("hidden");
  } else if (Notification.permission === "denied") {
    statusEl.textContent = "Blocked. Allow notifications for this site in your browser settings.";
  } else {
    statusEl.textContent = "Not enabled on this device yet.";
    enableBtn?.classList.remove("hidden");
    disableBtn?.classList.add("hidden");
  }
}

document.getElementById("push-enable-btn")?.addEventListener("click", async (e) => {
  setButtonLoading(e.currentTarget, true);
  try {
    await enablePush();
  } catch (err) {
    showToast(err.message || "Could not enable notifications", "error");
  } finally {
    setButtonLoading(e.currentTarget, false);
    refreshPushStatus();
    refreshDeviceList();
  }
});

document.getElementById("push-disable-btn")?.addEventListener("click", async (e) => {
  setButtonLoading(e.currentTarget, true);
  try {
    await disablePush();
  } finally {
    setButtonLoading(e.currentTarget, false);
    refreshPushStatus();
    refreshDeviceList();
  }
});

// Register early so an already-permitted device keeps working after a reload.
if (pushSupported() && Notification.permission === "granted") registerServiceWorker();
if (document.getElementById("push-status")) refreshPushStatus();
if (document.getElementById("device-list")) refreshDeviceList();
