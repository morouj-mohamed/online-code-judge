// app.js - startup code shared by every page: icons, theme toggle, command
// palette, the "current page" marker in the navigation, and the Ctrl/Cmd key
// labels (Mac users see ⌘).

import { api } from "./api.js";
import { hydrateIcons } from "./icons.js";
import { initPalette } from "./palette.js";
import { initTheme } from "./theme.js";
import { toast } from "./toast.js";

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

/** The account menu in the header: open/close, keyboard, and "Log out". */
function initUserMenu() {
  const trigger = document.querySelector(".user-trigger");
  const menu = document.getElementById("user-menu-list");
  if (!trigger || !menu) return;
  const items = () => [...menu.querySelectorAll("[role=menuitem]")];
  const open = (focusFirst) => {
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    if (focusFirst) items()[0]?.focus();
  };
  const close = (focusTrigger) => {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (focusTrigger) trigger.focus();
  };
  trigger.addEventListener("click", () => (menu.hidden ? open(false) : close(false)));
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      open(true);
    }
  });
  menu.addEventListener("keydown", (event) => {
    const list = items();
    const index = list.indexOf(document.activeElement);
    if (event.key === "Escape") close(true);
    else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      list[(index + delta + list.length) % list.length].focus();
    }
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !event.target.closest(".user-menu")) close(false);
  });
  menu.querySelector(".js-logout")?.addEventListener("click", async () => {
    try {
      await api.logout();
    } finally {
      window.location.href = "/";
    }
  });
}

export function boot() {
  hydrateIcons();
  initTheme();
  initPalette();
  initUserMenu();
  // A message left by the previous page (e.g. after logging in).
  const flash = sessionStorage.getItem("ocj-flash");
  if (flash) {
    sessionStorage.removeItem("ocj-flash");
    toast({ title: flash, kind: "success" });
  }
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
