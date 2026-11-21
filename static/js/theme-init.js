// theme-init.js - a tiny CLASSIC script loaded in <head> before the CSS paints.
// It picks the theme (saved choice, else the operating system preference) so
// the page never flashes the wrong colors. Everything else lives in theme.js.
(function () {
  var saved = null;
  try { saved = localStorage.getItem("ocj-theme"); } catch (e) { /* private mode */ }
  var system = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  var root = document.documentElement;
  root.setAttribute("data-theme", saved === "light" || saved === "dark" ? saved : system);
  root.classList.add("no-transitions");
  window.addEventListener("DOMContentLoaded", function () {
    requestAnimationFrame(function () { requestAnimationFrame(function () { root.classList.remove("no-transitions"); }); });
  });
})();
