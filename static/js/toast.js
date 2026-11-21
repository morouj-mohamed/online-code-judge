// toast.js - small, non-blocking notifications.
//
// The container is an aria-live region, so screen readers announce each toast
// without moving the keyboard focus.

import { icon } from "./icons.js";
import { escapeHtml } from "./format.js";

const ICONS = { success: "circle-check", error: "alert-octagon", warning: "alert", info: "info" };
let region = null;

function container() {
  if (!region) {
    region = document.createElement("div");
    region.className = "toasts";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-label", "Notifications");
    document.body.appendChild(region);
  }
  return region;
}

function dismiss(el) {
  if (!el.isConnected || el.classList.contains("is-leaving")) return;
  el.classList.add("is-leaving");
  const remove = () => el.remove();
  el.addEventListener("animationend", remove, { once: true });
  setTimeout(remove, 400);   // in case animations are disabled
}

/**
 * toast({title, message, kind: "success"|"error"|"warning"|"info", timeout, actions})
 * actions: [{label, onClick, primary}] - buttons inside the toast; a toast with
 * actions stays until it is answered or dismissed.
 */
export function toast({ title, message = "", kind = "info", timeout = 4500, actions = [] }) {
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.kind = kind;
  el.innerHTML = `${icon(ICONS[kind] || "info", { size: 18 })}
    <div><strong>${escapeHtml(title)}</strong>${message ? `<p>${escapeHtml(message)}</p>` : ""}
      ${actions.length ? `<div class="toast-actions">${actions.map((a, i) =>
        `<button class="btn btn-sm${a.primary ? " btn-primary" : ""}" type="button" data-action="${i}">${escapeHtml(a.label)}</button>`).join("")}</div>` : ""}</div>
    <button class="btn btn-ghost btn-sm btn-icon js-dismiss" type="button" aria-label="Dismiss notification">${icon("x", { size: 14 })}</button>`;
  el.querySelector(".js-dismiss").addEventListener("click", () => dismiss(el));
  el.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => {
    dismiss(el);
    actions[Number(button.dataset.action)].onClick();
  }));
  if (actions.length) timeout = 0;
  container().appendChild(el);
  if (timeout) setTimeout(() => dismiss(el), timeout);
  return el;
}
