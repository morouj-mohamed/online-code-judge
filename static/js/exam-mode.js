// exam-mode.js - "exam mode" on a contest problem page.
//
// A web page cannot STOP a student from switching to another tab or program;
// the browser does not allow that. What it can do:
//   * require fullscreen to work on the exam,
//   * NOTICE leaving (the page becomes hidden, the window loses focus, the
//     student leaves fullscreen) and large pastes, and report each one to the
//     server, which stores it with the time for the teacher,
//   * show the student how many exits are left before the exam is LOCKED.
// The lock itself is enforced by the server: a locked student's submissions
// are refused there, so hiding this overlay in the browser does not help.

import { api } from "./api.js";
import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";

const PASTE_LIMIT = 200;      // characters; typing is fine, pasting a whole solution is reported

export function startExamMode({ contest, onLockChange }) {
  let locked = false;
  let lastReport = 0;
  let overlay = null;

  function show(html, { blocking = true } = {}) {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "exam-overlay";
      overlay.setAttribute("role", "alertdialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.setAttribute("aria-labelledby", "exam-title");
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="exam-box card">${html}</div>`;
    overlay.dataset.blocking = String(blocking);
    overlay.hidden = false;
    overlay.querySelector("button")?.focus();
    overlay.querySelector(".js-fullscreen")?.addEventListener("click", enterFullscreen);
    overlay.querySelector(".js-continue")?.addEventListener("click", () => {
      if (document.fullscreenElement) hide();
      else enterFullscreen();
    });
  }
  function hide() {
    if (overlay && !locked) overlay.hidden = true;
  }

  function showLocked(status) {
    locked = true;
    onLockChange(true);
    show(`<span class="exam-icon" data-kind="locked">${icon("lock", { size: 28 })}</span>
      <h2 id="exam-title">Your exam is locked</h2>
      <p>You left the exam page ${status.exits} time${status.exits === 1 ? "" : "s"}. Submissions are closed for you
        until your teacher unlocks the exam. Everything was recorded with the time.</p>
      <p class="subtle">This page checks every few seconds and unlocks by itself when the teacher allows it.</p>`);
  }

  function enterFullscreen() {
    document.documentElement.requestFullscreen?.().then(hide).catch(() => {
      show(`<span class="exam-icon">${icon("alert", { size: 28 })}</span><h2 id="exam-title">Fullscreen was blocked</h2>
        <p>Your browser refused fullscreen. Allow it and try again.</p>
        <button class="btn btn-primary js-fullscreen" type="button">Try again</button>`);
    });
  }

  function applyStatus(status) {
    if (status.locked) return showLocked(status);
    if (locked) {
      locked = false;
      onLockChange(false);
      hide();
    }
    return null;
  }

  async function report(kind, detail = "") {
    if (locked) return;
    const now = Date.now();
    // Leaving a tab fires "hidden" AND "blur" together: count it once.
    if (kind !== "large_paste" && now - lastReport < 1500) return;
    if (kind !== "large_paste") lastReport = now;
    try {
      const status = await api.examEvent(contest, kind, detail);
      if (status.locked) return showLocked(status);
      if (kind !== "large_paste") {
        const left = status.exits_left;
        show(`<span class="exam-icon">${icon("alert", { size: 28 })}</span>
          <h2 id="exam-title">You left the exam</h2>
          <p>This was recorded with the time and your teacher can see it.
            ${left == null ? "" : `<strong>${left} exit${left === 1 ? "" : "s"} left</strong> before your exam is locked.`}</p>
          <button class="btn btn-primary js-continue" type="button">${icon("arrow-left")}Back to the exam (fullscreen)</button>`);
      }
    } catch {
      /* offline: nothing to do, the server enforces the lock anyway */
    }
    return null;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") report("tab_hidden");
  });
  window.addEventListener("blur", () => {
    // Focus moved to another program (focus moving inside the page does not fire this).
    setTimeout(() => { if (!document.hasFocus() && document.visibilityState === "visible") report("window_blur"); }, 300);
  });
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) report("fullscreen_exit");
  });
  document.addEventListener("paste", (event) => {
    const text = event.clipboardData?.getData("text") || "";
    if (text.length > PASTE_LIMIT) report("large_paste", `${text.length} characters`);
  }, true);

  // Start: already locked? Otherwise ask for fullscreen.
  api.examStatus(contest).then((status) => {
    if (status.locked) showLocked(status);
    else if (!document.fullscreenElement) {
      show(`<span class="exam-icon">${icon("lock", { size: 28 })}</span>
        <h2 id="exam-title">Exam mode</h2>
        <p>This contest is an exam. Work in fullscreen. Leaving the page — another tab, another program, leaving
          fullscreen — is recorded with the time${status.max_exits ? `, and after <strong>${escapeHtml(String(status.max_exits))}
          exit${status.max_exits === 1 ? "" : "s"}</strong> your exam is locked` : ""}.</p>
        <button class="btn btn-primary js-fullscreen" type="button">${icon("lock")}Start in fullscreen</button>`);
    }
  }).catch(() => {});

  // While locked, check every 8 s whether the teacher unlocked it.
  setInterval(() => {
    if (locked) api.examStatus(contest).then(applyStatus).catch(() => {});
  }, 8000);
}
