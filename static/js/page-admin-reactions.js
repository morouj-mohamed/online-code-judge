// page-admin-reactions.js - the admin page for verdict stickers / videos.
//
// One card per verdict: the built-in sticker, the uploaded reactions (with a
// preview and a delete button) and a small upload form.

import { api, describeError } from "./api.js";
import { announce, boot } from "./app.js";
import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";
import { showReaction, VERDICT_OF_KEY } from "./reactions.js";
import { STICKERS } from "./stickers.js";
import { toast } from "./toast.js";

boot();

const grid = document.querySelector(".js-verdicts");
const enabledBox = document.querySelector(".js-enabled");
let data = null;

const formatSize = (n) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

function thumb(r) {
  return r.kind === "video"
    ? `<video src="${escapeHtml(r.url)}" muted loop playsinline preload="metadata" aria-label="Uploaded video"></video>`
    : `<img src="${escapeHtml(r.url)}" alt="Uploaded picture${r.caption ? `: ${escapeHtml(r.caption)}` : ""}" loading="lazy">`;
}

function render() {
  enabledBox.checked = data.enabled;
  grid.innerHTML = Object.entries(data.reactions).map(([key, items]) => {
    const verdict = VERDICT_OF_KEY[key];
    return `<section class="card reaction-admin" data-key="${key}" aria-labelledby="rv-${key}">
      <header class="row-wrap"><h2 class="card-title" id="rv-${key}">${escapeHtml(verdict)}</h2><span class="spacer"></span>
        <span class="subtle">${items.length ? `${items.length} uploaded` : "built-in sticker"}</span></header>
      <ul class="reaction-items">
        <li class="reaction-item${items.length ? " is-unused" : ""}">
          <div class="reaction-thumb">${STICKERS[key].svg}</div>
          <div class="reaction-item-text"><strong>Built-in sticker</strong>
            <span class="subtle">${items.length ? "used only when nothing is uploaded" : "in use"}</span></div>
          <button class="btn btn-ghost btn-sm js-preview" type="button" data-id="builtin">${icon("play", { size: 14 })}Preview</button>
        </li>
        ${items.map((r) => `<li class="reaction-item">
          <div class="reaction-thumb">${thumb(r)}</div>
          <div class="reaction-item-text"><strong>${r.caption ? escapeHtml(r.caption) : `<span class="subtle">no caption</span>`}</strong>
            <span class="subtle">${r.kind} · ${formatSize(r.size)}</span></div>
          <div class="row"><button class="btn btn-ghost btn-sm js-preview" type="button" data-id="${r.id}">${icon("play", { size: 14 })}Preview</button>
          <button class="btn btn-ghost btn-sm btn-icon js-delete" type="button" data-id="${r.id}" aria-label="Delete this ${r.kind}">${icon("trash", { size: 14 })}</button></div>
        </li>`).join("")}
      </ul>
      <form class="reaction-upload js-upload" data-key="${key}">
        <label class="sr-only" for="file-${key}">Picture or video for ${escapeHtml(verdict)}</label>
        <input class="input" id="file-${key}" name="file" type="file" required accept="image/png,image/jpeg,image/gif,image/webp,video/mp4,video/webm">
        <label class="sr-only" for="cap-${key}">Caption (optional)</label>
        <input class="input" id="cap-${key}" name="caption" maxlength="80" placeholder="Caption (optional), e.g. عاش يا بطل!" dir="auto">
        <button class="btn btn-primary" type="submit">${icon("cloud", { size: 14 })}<span class="btn-label-idle">Upload</span><span class="btn-label-busy">Uploading…</span></button>
      </form>
    </section>`;
  }).join("");
  grid.setAttribute("aria-busy", "false");
}

async function load() {
  try {
    data = await api.request("GET", "/api/admin/reactions");
    render();
  } catch (error) {
    grid.innerHTML = `<div class="state" data-kind="error" role="alert"><h2>Could not load reactions</h2><p>${escapeHtml(describeError(error))}</p></div>`;
  }
}

enabledBox.addEventListener("change", async () => {
  try {
    data = await api.request("PUT", "/api/admin/reactions/settings", { enabled: enabledBox.checked });
    render();
    toast({ title: data.enabled ? "Reactions on for everyone" : "Reactions off for everyone", kind: "success" });
  } catch (error) {
    enabledBox.checked = !enabledBox.checked;
    toast({ title: "Not saved", message: describeError(error), kind: "error" });
  }
});

grid.addEventListener("click", async (event) => {
  const preview = event.target.closest(".js-preview");
  if (preview) {
    const key = preview.closest("[data-key]").dataset.key;
    const item = preview.dataset.id === "builtin" ? "builtin" : data.reactions[key].find((r) => String(r.id) === preview.dataset.id);
    showReaction(VERDICT_OF_KEY[key], { preview: item });
    return;
  }
  const del = event.target.closest(".js-delete");
  if (del) {
    if (!window.confirm("Delete this reaction? Students will no longer see it.")) return;
    try {
      await api.request("DELETE", `/api/admin/reactions/${del.dataset.id}`);
      await load();
      announce("Reaction deleted");
    } catch (error) {
      toast({ title: "Not deleted", message: describeError(error), kind: "error" });
    }
  }
});

grid.addEventListener("submit", async (event) => {
  const form = event.target.closest(".js-upload");
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  const body = new FormData(form);
  body.append("verdict", form.dataset.key);
  button.classList.add("is-busy");
  button.disabled = true;
  try {
    await api.request("POST", "/api/admin/reactions", body);
    await load();
    toast({ title: "Uploaded", message: `New reaction for ${VERDICT_OF_KEY[form.dataset.key]}.`, kind: "success" });
  } catch (error) {
    toast({ title: "Upload refused", message: describeError(error), kind: "error" });
    button.classList.remove("is-busy");
    button.disabled = false;
  }
});

load();
