// app.js - startup code shared by every page: icons, theme toggle, command
// palette, the "current page" marker in the navigation, and the Ctrl/Cmd key
// labels (Mac users see ⌘).

import { hydrateIcons } from "./icons.js";
import { initPalette } from "./palette.js";
import { initTheme } from "./theme.js";

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export function boot() {
  hydrateIcons();
  initTheme();
  initPalette();
  if (isMac) {
    document.querySelectorAll("[data-mod-key]").forEach((el) => {
      el.textContent = "⌘";
    });
  }
}

/** Show a value in an aria-live region (for screen readers). */
export function announce(text) {
  const region = document.getElementById("live-announcer");
  if (!region) return;
  region.textContent = "";
  // A tiny delay makes screen readers notice a repeated message.
  setTimeout(() => {
    region.textContent = text;
  }, 60);
}
