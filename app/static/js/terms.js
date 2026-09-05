/* ==========================================================================
   Agreeing to the terms

   Only for accounts that never saw the sign-up form -- Google sign-in creates
   one from the sign-in page, so the agreement has to be asked for afterwards.

   The checkbox is `required`, so the browser refuses an empty submit on its
   own and this file never has to police it. What it does police is the other
   half: the server records the agreement, and until that has actually landed
   nobody is sent anywhere. A page that navigates on the click and lets the
   request race behind it is a page that shows this screen again on the next
   load, having apparently accepted nothing.
   ========================================================================== */
(function () {
  "use strict";

  var form = document.getElementById("terms-accept-form");
  var button = document.getElementById("terms-continue");
  var error = document.getElementById("terms-error");

  function fail(message) {
    if (!error) return;
    error.textContent = message;
    error.classList.remove("hidden");
  }

  form?.addEventListener("submit", async function (e) {
    e.preventDefault();
    if (error) error.classList.add("hidden");
    if (typeof setButtonLoading === "function") setButtonLoading(button, true);

    try {
      await apiFetch("/api/auth/accept-terms", { method: "POST" });
      /* replace, not assign: the back button should not come back to a
         question that has already been answered. */
      window.location.replace("/dashboard");
    } catch (err) {
      if (typeof setButtonLoading === "function") setButtonLoading(button, false);
      fail((err && err.message) || "Could not record that. Please try again.");
    }
  });

  /* The way out. An account somebody can neither use nor leave is a trap, and
     declining has to be a real option or the agreement is not one. */
  document.getElementById("terms-decline")?.addEventListener("click", async function () {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch (_) {}
    window.location.replace("/login");
  });
})();
