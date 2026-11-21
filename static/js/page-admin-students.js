// page-admin-students.js - create many student accounts from a list or a CSV
// file, then download the passwords or print one login card per student.

import { api, describeError } from "./api.js";
import { boot } from "./app.js";
import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";
import { toast } from "./toast.js";

boot();

const textarea = document.getElementById("names");
const fileInput = document.getElementById("file");
const dropzone = document.querySelector(".js-dropzone");
const createButton = document.querySelector(".js-create");
const countEl = document.querySelector(".js-count");
const result = document.querySelector(".js-result");
let created = [];

/**
 * Turn text (typed, or a CSV saved by Excel) into [{username, password?}].
 * Accepts comma, semicolon or tab separators, ignores empty lines, a header
 * line ("username", "name", "user") and the UTF-8 marker Excel adds.
 */
function parse(text) {
  const rows = [];
  for (const raw of text.replace(/^﻿/, "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const cells = line.split(/[,;\t]/).map((c) => c.trim().replace(/^"|"$/g, ""));
    if (rows.length === 0 && /^(user ?name|username|name|user|student)$/i.test(cells[0])) continue;
    if (!cells[0]) continue;
    rows.push(cells[1] ? { username: cells[0], password: cells[1] } : { username: cells[0] });
  }
  return rows;
}

function refresh() {
  const rows = parse(textarea.value);
  countEl.textContent = rows.length ? `${rows.length} student${rows.length === 1 ? "" : "s"} ready to create.` : "No names yet.";
  createButton.disabled = rows.length === 0;
}

async function readFile(file) {
  if (!file) return;
  const text = await file.text();
  textarea.value = text.replace(/^﻿/, "");
  document.querySelector(".js-file-name").textContent = file.name;
  refresh();
}

textarea.addEventListener("input", refresh);
fileInput.addEventListener("change", () => readFile(fileInput.files[0]));
["dragenter", "dragover"].forEach((t) => dropzone.addEventListener(t, (e) => { e.preventDefault(); dropzone.classList.add("is-over"); }));
["dragleave", "drop"].forEach((t) => dropzone.addEventListener(t, () => dropzone.classList.remove("is-over")));
dropzone.addEventListener("drop", (e) => { e.preventDefault(); readFile(e.dataTransfer.files[0]); });

function renderResult(data) {
  created = data.created;
  const site = location.origin;
  document.querySelector(".js-cards").innerHTML = created.map((u) => `<li class="login-card">
      <p class="eyebrow">${escapeHtml(document.body.dataset.siteName || "")}</p>
      <p class="lc-row"><span>Website</span><span class="mono">${escapeHtml(site)}</span></p>
      <p class="lc-row"><span>Username</span><strong class="mono">${escapeHtml(u.username)}</strong></p>
      <p class="lc-row"><span>Password</span><strong class="mono">${escapeHtml(u.password)}</strong></p>
    </li>`).join("");
  document.querySelector(".js-skipped").innerHTML = data.skipped.length
    ? `<div class="form-error" role="status" style="margin-bottom: 16px">${icon("alert")}<span><strong>${data.skipped.length} skipped:</strong>
        ${data.skipped.map((s) => `${escapeHtml(s.username)} (${escapeHtml(s.reason)})`).join(" · ")}</span></div>` : "";
  result.hidden = created.length === 0 && data.skipped.length === 0;
  document.querySelector(".js-download").hidden = document.querySelector(".js-print").hidden = created.length === 0;
  result.scrollIntoView({ behavior: "smooth", block: "start" });
}

createButton.addEventListener("click", async () => {
  const rows = parse(textarea.value);
  createButton.classList.add("is-busy");
  createButton.disabled = true;
  try {
    const data = await api.admin.bulkUsers(rows);
    renderResult(data);
    toast({ title: `${data.created.length} account${data.created.length === 1 ? "" : "s"} created`,
      message: data.skipped.length ? `${data.skipped.length} skipped (see the list).` : "Download or print the passwords now.",
      kind: data.created.length ? "success" : "warning" });
    if (data.created.length) textarea.value = "";
  } catch (error) {
    toast({ title: "Could not create the accounts", message: describeError(error), kind: "error" });
  } finally {
    createButton.classList.remove("is-busy");
    refresh();
  }
});

document.querySelector(".js-download").addEventListener("click", () => {
  const lines = ["username,password,website", ...created.map((u) => `${u.username},${u.password},${location.origin}`)];
  // The UTF-8 marker makes Excel open the file with the right encoding.
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `student-accounts-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});
document.querySelector(".js-print").addEventListener("click", () => window.print());
refresh();
