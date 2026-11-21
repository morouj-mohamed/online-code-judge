// format.js - shared helpers: HTML escaping, verdict metadata, number and
// time formatting, and the count-up animation for metrics.

import { icon } from "./icons.js";

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Every status the API can return -> key (for CSS), label, short code, icon.
export const VERDICTS = {
  Accepted: { key: "ok", label: "Accepted", short: "AC", icon: "circle-check" },
  "Wrong Answer": { key: "wa", label: "Wrong Answer", short: "WA", icon: "circle-x" },
  "Time Limit Exceeded": { key: "tle", label: "Time Limit Exceeded", short: "TLE", icon: "clock" },
  "Memory Limit Exceeded": { key: "mle", label: "Memory Limit Exceeded", short: "MLE", icon: "memory" },
  "Runtime Error": { key: "re", label: "Runtime Error", short: "RE", icon: "alert" },
  "Compilation Error": { key: "ce", label: "Compilation Error", short: "CE", icon: "braces" },
  "Judge Error": { key: "error", label: "Judge Error", short: "JE", icon: "alert-octagon" },
  Pending: { key: "pending", label: "In queue", short: "Q", icon: "hourglass" },
  Running: { key: "running", label: "Running", short: "…", icon: "loader" },
};

export const FINAL = new Set(["Accepted", "Wrong Answer", "Time Limit Exceeded",
  "Memory Limit Exceeded", "Runtime Error", "Compilation Error", "Judge Error"]);

export function verdictInfo(status) {
  return VERDICTS[status] || { key: "pending", label: status || "Unknown", short: "?", icon: "info" };
}

/** A verdict badge: icon + text label (never color alone). */
export function verdictBadge(status, { large = false, short = false } = {}) {
  const v = verdictInfo(status);
  const text = short ? v.short : v.label;
  const title = short ? ` title="${escapeHtml(v.label)}"` : "";
  return `<span class="verdict${large ? " is-lg" : ""}" data-verdict="${v.key}"${title}>${icon(v.icon, { size: large ? 16 : 14 })}<span>${escapeHtml(text)}</span>${short ? `<span class="sr-only">${escapeHtml(v.label)}</span>` : ""}</span>`;
}

export function formatMs(ms) {
  if (ms == null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms)} ms`;
}

export function formatKb(kb) {
  if (kb == null) return "—";
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${Math.round(kb)} KB`;
}

export function splitUnit(text) {
  const match = /^([\d.,]+)\s*(.*)$/.exec(text);
  return match ? [match[1], match[2]] : [text, ""];
}

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
export function relativeTime(unixSeconds) {
  if (!unixSeconds) return "";
  const diff = unixSeconds - Date.now() / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function absoluteTime(unixSeconds) {
  return new Date(unixSeconds * 1000).toLocaleString();
}

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Animate a number from 0 to `to` inside `el` (count-up), formatted by
 * `format`. Respects prefers-reduced-motion by jumping straight to the value.
 */
export function countUp(el, to, format, duration = 700) {
  if (to == null || reducedMotion()) {
    el.innerHTML = format(to);
    return;
  }
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    el.innerHTML = format(to * eased);
    if (t < 1) requestAnimationFrame(step);
    else el.innerHTML = format(to);
  };
  requestAnimationFrame(step);
}

export const LANGUAGE_LABELS = { c: "C", cpp: "C++", python: "Python" };

export function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
