// admin-badges.js - small status badges shared by the admin pages.

import { absoluteTime, escapeHtml } from "./format.js";
import { icon } from "./icons.js";

export function stateBadge(hidden) {
  return hidden
    ? `<span class="state-badge" data-state="hidden">${icon("eye-off", { size: 13 })}Hidden</span>`
    : `<span class="state-badge" data-state="published">${icon("eye", { size: 13 })}Published</span>`;
}

export function validationBadge(v) {
  if (!v) return `<span class="state-badge">${icon("info", { size: 13 })}Not checked</span>`;
  const ok = v.verdict === "Accepted";
  return `<span class="state-badge" data-state="${ok ? "published" : "hidden"}" title="${escapeHtml(absoluteTime(v.at))}">
    ${icon(ok ? "shield" : "alert", { size: 13 })}${ok ? "Reference passes" : escapeHtml(v.verdict)}</span>`;
}
