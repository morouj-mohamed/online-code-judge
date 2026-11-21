// theme.js - light/dark theme toggle.
//
// Default: follow the operating system (and keep following it live). As soon
// as the user clicks the toggle, their choice is saved and wins.

const KEY = "ocj-theme";
const media = window.matchMedia("(prefers-color-scheme: light)");

export function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "dark";
}

function apply(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  document.querySelectorAll(".theme-toggle").forEach((button) => {
    const next = theme === "dark" ? "light" : "dark";
    button.setAttribute("aria-label", `Switch to ${next} theme`);
    button.setAttribute("title", `Switch to ${next} theme`);
  });
  window.dispatchEvent(new CustomEvent("themechange", { detail: { theme } }));
}

export function setTheme(theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* storage unavailable: still switch for this page */
  }
  apply(theme);
}

export function toggleTheme() {
  setTheme(currentTheme() === "dark" ? "light" : "dark");
}

export function initTheme() {
  apply(currentTheme());
  document.querySelectorAll(".theme-toggle").forEach((button) => {
    button.addEventListener("click", toggleTheme);
  });
  media.addEventListener("change", (event) => {
    let saved = null;
    try {
      saved = localStorage.getItem(KEY);
    } catch {
      /* ignore */
    }
    if (!saved) apply(event.matches ? "light" : "dark");
  });
}
