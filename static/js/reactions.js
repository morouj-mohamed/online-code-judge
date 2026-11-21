// reactions.js - a sticker or short video when a verdict arrives live.
//
// Picks, for the verdict, one of the teacher's uploads at random (if there are
// any) or the built-in comic sticker. It is a small card in the corner, not a
// dialog: it never blocks the page, closes by itself, and stays open while
// the mouse or the keyboard focus is on it.
//
// It is NOT shown when: the teacher switched reactions off, this browser
// switched them off ("Stop showing these"), or the submission belongs to an
// exam (exam mode), where a celebration would distract other students.

import { api } from "./api.js";
import { announce } from "./app.js";
import { escapeHtml, verdictInfo } from "./format.js";
import { icon } from "./icons.js";
import { STICKERS } from "./stickers.js";
import { toast } from "./toast.js";

const PREF = "ocj-reactions";            // localStorage: "off" = never show
const CLOSE_AFTER_MS = 7000;
const VIDEO_CLOSE_AFTER_MS = 15000;

const KEYS = {
  "Accepted": "accepted",
  "Wrong Answer": "wrong_answer",
  "Time Limit Exceeded": "time_limit",
  "Memory Limit Exceeded": "memory_limit",
  "Runtime Error": "runtime_error",
  "Compilation Error": "compilation_error",
};

export function reactionsEnabledHere() {
  try { return localStorage.getItem(PREF) !== "off"; } catch { return true; }
}

export function setReactionsHere(on) {
  try { localStorage.setItem(PREF, on ? "on" : "off"); } catch { /* private mode */ }
}

const pick = (list) => list[Math.floor(Math.random() * list.length)];

let current = null;

function close(card) {
  if (!card || !card.isConnected) return;
  card.classList.add("is-leaving");
  card.querySelector("video")?.pause();
  const done = () => card.remove();
  card.addEventListener("animationend", done, { once: true });
  setTimeout(done, 400);                 // reduced motion: no animationend
  if (current === card) current = null;
}

/**
 * Show the reaction for a final verdict.
 * options.examMode: the submission is part of an exam -> nothing is shown.
 */
export const VERDICT_OF_KEY = Object.fromEntries(Object.entries(KEYS).map(([v, k]) => [k, v]));

export async function showReaction(verdict, { examMode = false, preview } = {}) {
  const key = KEYS[verdict];
  if (!key) return;
  let custom = null;
  if (preview) {
    // The admin page previews one exact reaction (or the built-in sticker),
    // whatever the switches say.
    custom = preview === "builtin" ? null : preview;
  } else {
    if (examMode || !reactionsEnabledHere()) return;
    let site = { enabled: true, reactions: {} };
    try {
      site = await api.request("GET", "/api/reactions");
    } catch {
      /* offline: the built-in stickers still work */
    }
    if (!site.enabled) return;
    const uploads = site.reactions?.[key] || [];
    custom = uploads.length ? pick(uploads) : null;
  }
  const builtin = STICKERS[key];
  const caption = custom?.caption || pick(builtin.captions);
  const v = verdictInfo(verdict);

  let media;
  if (!custom) media = builtin.svg;
  else if (custom.kind === "video") {
    media = `<video class="reaction-media" src="${escapeHtml(custom.url)}" autoplay muted loop playsinline
      aria-label="Reaction video for ${escapeHtml(verdict)}"></video>`;
  } else {
    media = `<img class="reaction-media" src="${escapeHtml(custom.url)}" alt="Reaction for ${escapeHtml(verdict)}${caption ? `: ${escapeHtml(caption)}` : ""}">`;
  }

  close(current);
  const card = document.createElement("aside");
  card.className = "reaction-card";
  card.dataset.verdict = key;
  card.setAttribute("aria-label", `Reaction: ${verdict}`);
  card.innerHTML = `
    <button class="btn btn-ghost btn-icon btn-sm reaction-close" type="button" aria-label="Close reaction">${icon("x", { size: 14 })}</button>
    <div class="reaction-stage">${media}</div>
    <div class="reaction-text">
      <p class="reaction-verdict" style="color: var(--v-${v.key})">${icon(v.icon, { size: 15 })}<strong>${escapeHtml(verdict)}</strong></p>
      ${caption ? `<p class="reaction-caption">${escapeHtml(caption)}</p>` : ""}
      <div class="row-wrap reaction-actions">
        ${custom?.kind === "video" ? `<button class="btn btn-sm js-unmute" type="button" aria-pressed="false">${icon("volume", { size: 14 })}<span>Sound on</span></button>` : ""}
        <button class="btn btn-ghost btn-sm js-never" type="button">Stop showing these</button>
      </div>
    </div>
    <span class="reaction-timer" aria-hidden="true"></span>`;
  document.body.append(card);
  current = card;

  // Auto-close, paused while the user hovers or focuses the card.
  const total = custom?.kind === "video" ? VIDEO_CLOSE_AFTER_MS : CLOSE_AFTER_MS;
  let left = total;
  let started = performance.now();
  let timer = setTimeout(() => close(card), left);
  card.style.setProperty("--reaction-ms", `${total}ms`);
  const pause = () => {
    if (!timer) return;
    clearTimeout(timer); timer = null;
    left -= performance.now() - started;
    card.classList.add("is-paused");
  };
  const resume = () => {
    if (timer || card.matches(":hover") || card.contains(document.activeElement)) return;
    started = performance.now();
    timer = setTimeout(() => close(card), Math.max(1500, left));
    card.classList.remove("is-paused");
  };
  card.addEventListener("mouseenter", pause);
  card.addEventListener("mouseleave", resume);
  card.addEventListener("focusin", pause);
  card.addEventListener("focusout", () => setTimeout(resume, 0));

  card.querySelector(".reaction-close").addEventListener("click", () => close(card));
  card.addEventListener("keydown", (event) => { if (event.key === "Escape") close(card); });
  const onEscape = (event) => {
    if (event.key === "Escape" && current === card && !document.querySelector("dialog[open]")) close(card);
  };
  document.addEventListener("keydown", onEscape);
  card.addEventListener("animationend", () => { if (!card.isConnected) document.removeEventListener("keydown", onEscape); });

  card.querySelector(".js-never").addEventListener("click", () => {
    setReactionsHere(false);
    close(card);
    toast({ title: "Reactions turned off", message: "Turn them back on any time: Ctrl K → “Turn verdict reactions on”.", kind: "info" });
  });
  const unmute = card.querySelector(".js-unmute");
  unmute?.addEventListener("click", () => {
    const video = card.querySelector("video");
    video.muted = !video.muted;
    if (!video.muted) video.play().catch(() => {});
    unmute.setAttribute("aria-pressed", String(!video.muted));
    unmute.querySelector("span").textContent = video.muted ? "Sound on" : "Sound off";
  });

  if (caption) announce(caption);
}
