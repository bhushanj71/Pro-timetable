/* Google Calendar connection panel on the profile page. */

async function refreshGoogleCard() {
  const card = document.getElementById("google-card");
  if (!card) return;

  const { enabled } = await fetch("/auth/google/status").then((r) => r.json()).catch(() => ({ enabled: false }));
  if (!enabled) {
    // Server has no OAuth credentials — hide rather than offer a dead link.
    card.classList.add("hidden");
    return;
  }

  const me = await apiFetch("/api/auth/me");
  const connected = !!me.google_sync_enabled || !!me.google_connected;

  document.getElementById("google-connected-badge").classList.toggle("hidden", !connected);
  document.getElementById("google-connected").classList.toggle("hidden", !connected);
  document.getElementById("google-not-connected").classList.toggle("hidden", connected);
  const toggle = document.getElementById("pf-google-sync");
  if (toggle) toggle.checked = !!me.google_sync_enabled;
}

document.getElementById("pf-google-sync")?.addEventListener("change", async (e) => {
  try {
    await apiFetch("/api/google/toggle-sync", { method: "POST", body: { enabled: e.target.checked } });
    showToast(e.target.checked ? "Google Calendar sync on" : "Google Calendar sync off", "success");
  } catch (err) {
    e.target.checked = !e.target.checked; // revert the optimistic flip
    showToast(err.message, "error");
  }
});

document.getElementById("pf-google-sync-now")?.addEventListener("click", async (e) => {
  const out = document.getElementById("pf-google-result");
  setButtonLoading(e.currentTarget, true);
  out.textContent = "";
  try {
    const r = await apiFetch("/api/google/sync-now", { method: "POST" });
    out.innerHTML = `Synced <strong>${r.synced}</strong> of ${r.total} upcoming event(s)` +
      (r.failed ? ` · <span style="color:var(--danger)">${r.failed} failed</span>` : "");
    showToast(`Synced ${r.synced} event(s) to Google Calendar`, "success");
  } catch (err) {
    out.textContent = err.message;
  } finally {
    setButtonLoading(e.currentTarget, false);
  }
});

document.getElementById("pf-google-disconnect")?.addEventListener("click", async () => {
  if (!confirm("Disconnect Google Calendar?\n\nEvents already copied there will stay, but new changes won't sync.")) return;
  try {
    await apiFetch("/api/google/disconnect", { method: "POST" });
    showToast("Google Calendar disconnected", "success");
    refreshGoogleCard();
  } catch (err) {
    showToast(err.message, "error");
  }
});

// Feedback after returning from Google's consent screen.
(() => {
  const p = new URLSearchParams(location.search);
  if (p.get("google") === "linked") showToast("✓ Google Calendar connected", "success");
  if (p.get("google_error") === "already_linked") showToast("That Google account is already linked to another user.", "error");
})();

refreshGoogleCard();

/* ---------------- Platform guide tabs ---------------- */

// Open the tab matching the visitor's device, since that's the guide they need.
(() => {
  const buttons = document.querySelectorAll(".tab-btn");
  if (!buttons.length) return;

  const show = (name) => {
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${name}`));
  };

  buttons.forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));

  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    // iPadOS 13+ reports as Mac, so check for touch support too.
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  show(isIOS ? "ios" : "android");
})();
