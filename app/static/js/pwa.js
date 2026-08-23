/* Install-to-home-screen handling.

   Notifications are delivered by Web Push from the installed app, so getting
   the app onto the home screen is the whole setup. Android/Chrome exposes a
   real install prompt; iOS has no API for it and must be talked through
   Share -> Add to Home Screen. */

let deferredInstallPrompt = null;

const isStandalone = () =>
  window.matchMedia("(display-mode: standalone)").matches ||
  window.navigator.standalone === true;

const isIOSDevice = () =>
  /iphone|ipad|ipod/i.test(navigator.userAgent) ||
  // iPadOS 13+ reports as a Mac, so fall back to touch support.
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

// Chrome fires this instead of showing its own banner; stash it so our own
// button can trigger the real prompt later.
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  document.dispatchEvent(new CustomEvent("pwa-installable"));
});

window.addEventListener("appinstalled", () => {
  deferredInstallPrompt = null;
  document.dispatchEvent(new CustomEvent("pwa-installed"));
  showToast("✓ App installed. Open it from your home screen to finish setup.", "success");
});

/** Returns "installed" | "prompted" | "ios" | "unavailable". */
async function promptInstall() {
  if (isStandalone()) return "installed";

  if (deferredInstallPrompt) {
    deferredInstallPrompt.prompt();
    const { outcome } = await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    return outcome === "accepted" ? "prompted" : "unavailable";
  }

  // No programmatic install on iOS — the caller shows the manual steps.
  return isIOSDevice() ? "ios" : "unavailable";
}

/** One-line description of where this device stands. */
function installStatusText() {
  if (isStandalone()) return "✓ Installed — you're running the app from your home screen.";
  if (isIOSDevice()) return "Tap Share, then Add to Home Screen, and reopen from that icon.";
  if (deferredInstallPrompt) return "Ready to install on this device.";
  return "Open this site in Chrome on your phone to install it.";
}
