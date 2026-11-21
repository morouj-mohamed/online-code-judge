// page-contests.js - all contests, grouped: running now, upcoming, finished.

import { api, describeError } from "./api.js";
import { boot } from "./app.js";
import { escapeHtml, formatCountdown, formatDateTime, startCountdown } from "./format.js";
import { icon } from "./icons.js";

boot();

const region = document.getElementById("contests-region");
const PHASE = {
  running: { label: "Running", icon: "activity" },
  upcoming: { label: "Upcoming", icon: "calendar" },
  ended: { label: "Finished", icon: "check" },
};

function card(c) {
  const p = PHASE[c.phase];
  return `<li class="contest-card" data-phase="${c.phase}">
    <div class="row-wrap">
      <span class="phase-pill" data-phase="${c.phase}">${icon(p.icon, { size: 13 })}${p.label}</span>
      ${c.exam_mode ? `<span class="state-badge" data-state="hidden">${icon("lock", { size: 13 })}Exam mode</span>` : ""}
      ${c.hidden ? `<span class="state-badge">${icon("eye-off", { size: 13 })}Hidden</span>` : ""}
    </div>
    <h2><a class="contest-link" href="/contests/${encodeURIComponent(c.slug)}">${escapeHtml(c.title)}</a></h2>
    <p class="muted">${escapeHtml(formatDateTime(c.starts_at))} → ${escapeHtml(formatDateTime(c.ends_at))}</p>
    <div class="row-wrap">
      <span class="chip">${icon("file-code", { size: 13 })}<span class="num">${c.problem_count}</span> problems</span>
      ${c.phase !== "ended" ? `<span class="chip">${icon("clock", { size: 13 })}${c.phase === "running" ? "ends in" : "starts in"}
        <span class="num js-cd" data-slug="${escapeHtml(c.slug)}">—</span></span>` : ""}
    </div>
  </li>`;
}

async function load() {
  try {
    const data = await api.contests();
    region.setAttribute("aria-busy", "false");
    if (!data.contests.length) {
      region.innerHTML = `<div class="state"><span class="state-icon">${icon("trophy", { size: 22 })}</span>
        <h2>No contests yet</h2><p>When your teacher schedules one, it appears here.</p></div>`;
      return;
    }
    const order = ["running", "upcoming", "ended"];
    const titles = { running: "Running now", upcoming: "Upcoming", ended: "Finished" };
    region.innerHTML = order.map((phase) => {
      const list = data.contests.filter((c) => c.phase === phase);
      if (phase === "ended") list.sort((a, b) => b.ends_at - a.ends_at);
      else list.sort((a, b) => a.starts_at - b.starts_at);
      return list.length ? `<section class="contest-section" aria-labelledby="sec-${phase}">
        <h2 class="eyebrow" id="sec-${phase}">${titles[phase]}</h2>
        <ul class="contest-grid">${list.map(card).join("")}</ul></section>` : "";
    }).join("");
    for (const c of data.contests) {
      const el = region.querySelector(`.js-cd[data-slug="${CSS.escape(c.slug)}"]`);
      if (!el) continue;
      startCountdown({ startsAt: c.starts_at, endsAt: c.ends_at, serverTime: data.server_time }, (left, phase, before) => {
        el.textContent = formatCountdown(left);
        if (before && before !== phase) load();          // a contest started or ended: refresh
      });
    }
  } catch (error) {
    region.setAttribute("aria-busy", "false");
    region.innerHTML = `<div class="state" data-kind="error" role="alert"><span class="state-icon">${icon("offline")}</span>
      <h2>Could not load contests</h2><p>${escapeHtml(describeError(error))}</p></div>`;
  }
}

load();
