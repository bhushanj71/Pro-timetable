/* Landing page: types the example prompt so visitors see the core
   interaction before they sign up. */

(() => {
  const target = document.getElementById("lp-typed");
  if (!target) return;

  const phrase =
    "I have ANN lecture every Monday, Wednesday and Friday from 10 to 11 AM. Remind me 30 minutes before.";

  // Respect reduced-motion: show the finished sentence rather than animating.
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    target.textContent = phrase;
    return;
  }

  let i = 0;
  const tick = () => {
    target.textContent = phrase.slice(0, ++i);
    if (i < phrase.length) {
      // Slight jitter reads as typing rather than a machine ticker.
      setTimeout(tick, 26 + Math.random() * 34);
    }
  };
  setTimeout(tick, 450);
})();

// Smooth-scroll the in-page nav links.
document.querySelectorAll('.lp-nav-links a[href^="#"]').forEach((a) =>
  a.addEventListener("click", (e) => {
    const el = document.querySelector(a.getAttribute("href"));
    if (!el) return;
    e.preventDefault();
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  })
);
