// announcements.js - messages the teacher posts during a contest.
//
// Open contest pages and problem pages of a contest ask the server every 15 s
// for announcements newer than the last one they saw ("?after=<id>"): a tiny
// request that is usually empty. A new one appears as a toast that stays
// until it is closed (and is read out by screen readers); the contest page
// also keeps the full list. What this browser already saw is remembered, so
// reopening the page does not pop the old ones up again.

import { api } from "./api.js";
import { announce } from "./app.js";
import { escapeHtml, formatDateTime } from "./format.js";
import { icon } from "./icons.js";
import { toast } from "./toast.js";

const POLL_MS = 15000;

const seenKey = (slug) => `ocj-ann-${slug}`;
function lastSeen(slug) {
  try { return Number(localStorage.getItem(seenKey(slug))) || 0; } catch { return 0; }
}
function markSeen(slug, id) {
  try { if (id > lastSeen(slug)) localStorage.setItem(seenKey(slug), String(id)); } catch { /* private mode */ }
}

export function renderAnnouncementList(items, { admin = false } = {}) {
  if (!items.length) return "";
  return [...items].reverse().map((a) => `<li class="announcement" data-id="${a.id}">
      <p class="announcement-body">${escapeHtml(a.body)}</p>
      <p class="announcement-meta subtle">${icon("clock", { size: 12 })}<time datetime="${new Date(a.created_at * 1000).toISOString()}">${escapeHtml(formatDateTime(a.created_at))}</time>
        ${admin && a.created_by ? ` · ${escapeHtml(a.created_by)}` : ""}
        ${admin ? `<button class="btn btn-ghost btn-sm js-ann-delete" type="button" data-id="${a.id}" aria-label="Delete this announcement">${icon("trash", { size: 13 })}Delete</button>` : ""}</p>
    </li>`).join("");
}

/**
 * Start watching a contest's announcements.
 *   onList(items)  called with the whole list whenever it changes (contest page)
 *   toastNew       show new ones as toasts (problem pages, and the contest page)
 * Returns { refresh(), stop() }.
 */
export function watchAnnouncements(slug, { onList = null, toastNew = true } = {}) {
  let items = [];
  let timer = null;
  let first = true;

  async function refresh() {
    try {
      const after = items.length ? items[items.length - 1].id : 0;
      const r = await api.request("GET", `/api/contests/${encodeURIComponent(slug)}/announcements?after=${after}`);
      if (r.announcements.length) {
        items = items.concat(r.announcements);
        const seen = lastSeen(slug);
        const fresh = r.announcements.filter((a) => a.id > seen);
        if (toastNew && fresh.length && !(first && !seen && fresh.length > 3)) {
          for (const a of fresh) {
            toast({ title: "Announcement from your teacher", message: a.body, kind: "warning", timeout: 0 });
          }
          announce(`Announcement: ${fresh.map((a) => a.body).join(". ")}`);
        }
        markSeen(slug, items[items.length - 1].id);
        onList?.(items);
      } else if (first) {
        onList?.(items);
      }
    } catch {
      /* offline or the contest was hidden: try again later */
    }
    first = false;
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(async () => { await refresh(); schedule(); }, POLL_MS);
  }
  refresh().then(schedule);
  return {
    async replace(list) { items = list; markSeen(slug, list.length ? list[list.length - 1].id : 0); onList?.(items); },
    refresh,
    stop() { clearTimeout(timer); },
  };
}
