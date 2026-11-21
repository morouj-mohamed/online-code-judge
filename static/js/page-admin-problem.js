// page-admin-problem.js - create or edit one problem.
//
//   1. Details + statement (Markdown with a live preview) + public sample -> Save
//   2. Hidden tests: upload a ZIP (checked on the server before it replaces anything)
//   3. Run a reference solution on every test, in the real sandbox
//   4. Publish / hide

import { api, describeError } from "./api.js";
import { announce, boot } from "./app.js";
import { createEditor, starterCode } from "./editor.js";
import { debounce, escapeHtml, formatKb, formatMs, verdictBadge } from "./format.js";
import { icon } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import { stateBadge, validationBadge } from "./admin-badges.js";
import { getDraft, saveDraft } from "./store.js";
import { toast } from "./toast.js";

const root = document.getElementById("admin-problem");
let slug = root.dataset.slug;
const form = document.querySelector(".js-form");
const errorBox = form.querySelector(".js-error");
const saveButton = form.querySelector(".js-save");
const field = (name) => form.elements.namedItem(name);
let problem = null;
let editor = null;

const NEW_STATEMENT = `# Title

Describe the task in one or two sentences.

## Input
What the input contains, with limits like \`1 <= n <= 10^5\`.

## Output
What to print.
`;

// ------------------------------------------------------------------ helpers
function busy(button, on) {
  button.classList.toggle("is-busy", on);
  button.disabled = on;
}

function showError(message) {
  errorBox.innerHTML = `${icon("alert-octagon")}<span>${escapeHtml(message)}</span>`;
  errorBox.hidden = false;
  errorBox.scrollIntoView({ block: "center", behavior: "smooth" });
}

const updatePreview = debounce(() => {
  document.querySelector(".js-preview").innerHTML =
    renderMarkdown(field("statement").value || "*Nothing yet*", { skipTitle: true }) ||
    '<p class="subtle">The statement preview appears here.</p>';
}, 120);

function slugify(text) {
  return text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

function formData() {
  return {
    slug: field("slug").value.trim(),
    index: field("index").value.trim().toUpperCase(),
    title: field("title").value.trim(),
    difficulty: field("difficulty").value,
    tags: field("tags").value.split(",").map((t) => t.trim()).filter(Boolean),
    time_limit_ms: Number(field("time_limit_ms").value),
    memory_limit_mb: Number(field("memory_limit_mb").value),
    statement: field("statement").value,
    sample_input: field("sample_input").value,
    sample_output: field("sample_output").value,
  };
}

function fill(p) {
  field("slug").value = p.slug;
  field("index").value = p.index;
  field("title").value = p.title;
  field("difficulty").value = p.difficulty;
  field("tags").value = p.tags.join(", ");
  field("time_limit_ms").value = p.time_limit_ms;
  field("memory_limit_mb").value = p.memory_limit_mb;
  field("statement").value = p.statement;
  field("sample_input").value = p.sample_input;
  field("sample_output").value = p.sample_output;
  document.querySelector(".js-slug-preview").textContent = p.slug;
  updatePreview();
}

// ------------------------------------------------------------- status area
function renderStatus() {
  if (!problem) return;
  document.querySelector(".js-heading").textContent = `${problem.index}. ${problem.title}`;
  document.querySelector(".js-badges").innerHTML = `${stateBadge(problem.hidden)} ${validationBadge(problem.validation)}
    <span class="chip">${icon("list-checks", { size: 13 })}<span class="num">${problem.tests.count}</span> tests · ${formatKb(Math.ceil(problem.tests.total_bytes / 1024))}</span>`;
  document.querySelector(".js-tests-count").innerHTML =
    `${icon("list-checks", { size: 13 })}<span class="num">${problem.tests.count}</span> tests uploaded`;
  document.querySelector(".js-validation-badge").innerHTML = validationBadge(problem.validation);

  const actions = document.querySelector(".js-head-actions");
  const canPublish = problem.tests.count > 0;
  actions.innerHTML = `
    <a class="btn btn-ghost" href="/problems/${encodeURIComponent(problem.slug)}">${icon("eye")}Preview</a>
    ${problem.hidden
      ? `<button class="btn btn-primary js-visibility" type="button" ${canPublish ? "" : "disabled"}
           title="${canPublish ? "Make it visible to everyone" : "Upload tests first"}">${icon("eye")}Publish</button>`
      : `<button class="btn js-visibility" type="button">${icon("eye-off")}Hide</button>`}`;
  actions.querySelector(".js-visibility").addEventListener("click", toggleVisibility);
}

async function toggleVisibility() {
  const publishing = problem.hidden;
  if (publishing && problem.validation?.verdict !== "Accepted" &&
      !window.confirm("No reference solution has passed all tests yet. Publish anyway?")) return;
  try {
    problem = await api.admin.visibility(slug, !publishing);
    renderStatus();
    toast({ title: publishing ? "Published" : "Hidden", message: publishing
      ? "Everyone can see and solve it now." : "Only admins can see it now.", kind: "success" });
  } catch (error) {
    toast({ title: "Could not change visibility", message: describeError(error), kind: "error" });
  }
}

// --------------------------------------------------------------------- save
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  busy(saveButton, true);
  try {
    if (slug) {
      problem = await api.admin.update(slug, formData());
      toast({ title: "Saved", kind: "success" });
    } else {
      problem = await api.admin.create(formData());
      slug = problem.slug;
      history.replaceState(null, "", `/admin/problems/${encodeURIComponent(slug)}`);
      root.dataset.slug = slug;
      field("slug").readOnly = true;
      saveButton.querySelector(".btn-label-idle").textContent = "Save changes";
      document.querySelectorAll(".js-tests-card, .js-validate-card, .js-danger").forEach((el) => { el.hidden = false; });
      toast({ title: "Problem created", message: "It stays hidden until you publish it. Next: upload the tests.", kind: "success" });
      initValidateEditor();
    }
    fill(problem);
    renderStatus();
    document.querySelector(".js-save-note").textContent = `Saved ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    showError(describeError(error));
  } finally {
    busy(saveButton, false);
  }
});

// -------------------------------------------------------------------- tests
const zipInput = document.getElementById("f-zip");
const dropzone = document.querySelector(".js-dropzone");
const uploadButton = document.querySelector(".js-upload");
let chosenFile = null;

function chooseFile(file) {
  if (!file) return;
  if (!/\.zip$/i.test(file.name)) {
    toast({ title: "Not a ZIP file", message: "Pack the tests as 1.in, 1.out, 2.in, … into a .zip", kind: "warning" });
    return;
  }
  chosenFile = file;
  document.querySelector(".js-zip-name").textContent = `${file.name} · ${formatKb(file.size / 1024)}`;
  uploadButton.disabled = false;
}

zipInput.addEventListener("change", () => chooseFile(zipInput.files[0]));
["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => {
  event.preventDefault();
  dropzone.classList.add("is-over");
}));
["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, () => dropzone.classList.remove("is-over")));
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  chooseFile(event.dataTransfer.files[0]);
});

uploadButton.addEventListener("click", async () => {
  if (!chosenFile) return;
  busy(uploadButton, true);
  try {
    problem = await api.admin.uploadTests(slug, chosenFile);
    renderStatus();
    toast({ title: `${problem.uploaded.count} tests uploaded`, message: "Now check them with a reference solution.", kind: "success" });
    announce(`${problem.uploaded.count} tests uploaded`);
    chosenFile = null;
    zipInput.value = "";
    document.querySelector(".js-zip-name").textContent = "max 32 MB, up to 200 tests";
  } catch (error) {
    toast({ title: "Upload refused", message: describeError(error), kind: "error", timeout: 8000 });
  } finally {
    busy(uploadButton, false);
    uploadButton.disabled = !chosenFile;
  }
});

// ----------------------------------------------------------------- validate
const languageSelect = document.getElementById("v-language");
const validateButton = document.querySelector(".js-validate");

async function initValidateEditor() {
  if (editor) return;
  const language = languageSelect.value;
  editor = await createEditor(document.getElementById("v-editor"), {
    doc: getDraft(`admin-${slug}`, language) || starterCode(language),
    language,
    fontSize: 13,
    onChange: debounce((code) => saveDraft(`admin-${slug}`, languageSelect.value, code), 400),
    onRun: () => validate(),
    onSubmit: () => validate(),
  });
}

languageSelect.addEventListener("change", () => {
  if (!editor) return;
  editor.setLanguage(languageSelect.value);
  editor.setValue(getDraft(`admin-${slug}`, languageSelect.value) || starterCode(languageSelect.value));
});

async function validate() {
  if (!editor || validateButton.disabled) return;
  const out = document.querySelector(".js-validate-result");
  busy(validateButton, true);
  out.innerHTML = `<p class="muted row">${icon("loader", { cls: "spinner" })}Running on every hidden test…</p>`;
  try {
    const r = await api.admin.validate(slug, languageSelect.value, editor.getValue());
    const rows = r.tests.map((t) => `<tr><td class="mono">#${t.test}</td><td>${verdictBadge(t.verdict)}</td>
      <td class="num">${formatMs(t.time_ms)}</td><td class="num">${formatKb(t.memory_kb)}</td>
      <td class="msg">${escapeHtml(t.message)}</td></tr>`).join("");
    out.innerHTML = `<div class="row-wrap" style="margin-bottom: 12px">${verdictBadge(r.verdict, { large: true })}
        <span class="muted">${r.verdict === "Accepted" ? `All ${r.total_tests} tests passed. Safe to publish.`
          : escapeHtml(r.message)}</span></div>
      ${r.compile_output && r.verdict === "Compilation Error" ? `<pre class="console">${escapeHtml(r.compile_output)}</pre>` : ""}
      ${rows ? `<div class="table-wrap"><table class="table"><thead><tr><th scope="col">Test</th><th scope="col">Verdict</th>
        <th scope="col" class="num">CPU</th><th scope="col" class="num">Memory</th><th scope="col">Details (admin only)</th></tr></thead>
        <tbody>${rows}</tbody></table></div>` : ""}`;
    problem = await api.admin.problem(slug);
    renderStatus();
    announce(`Reference solution: ${r.verdict}`);
  } catch (error) {
    out.innerHTML = `<div class="form-error" role="alert">${icon("alert-octagon")}<span>${escapeHtml(describeError(error))}</span></div>`;
  } finally {
    busy(validateButton, false);
  }
}
validateButton.addEventListener("click", validate);

// ------------------------------------------------------------------- delete
const confirmInput = document.getElementById("delete-confirm");
const deleteButton = document.querySelector(".js-delete");
confirmInput.addEventListener("input", () => {
  deleteButton.disabled = confirmInput.value.trim() !== slug;
});
deleteButton.addEventListener("click", async () => {
  busy(deleteButton, true);
  try {
    await api.admin.remove(slug);
    sessionStorage.setItem("ocj-flash", `Problem “${slug}” deleted.`);
    window.location.href = "/admin";
  } catch (error) {
    toast({ title: "Could not delete", message: describeError(error), kind: "error" });
    busy(deleteButton, false);
  }
});

// -------------------------------------------------------------------- start
field("statement").addEventListener("input", updatePreview);
field("title").addEventListener("input", () => {
  if (!slug && !field("slug").dataset.touched) {
    field("slug").value = slugify(field("title").value);
    document.querySelector(".js-slug-preview").textContent = field("slug").value || "…";
  }
});
field("slug").addEventListener("input", () => {
  field("slug").dataset.touched = "1";
  document.querySelector(".js-slug-preview").textContent = field("slug").value || "…";
});

async function start() {
  boot();
  if (!slug) {
    const overview = await api.admin.problems().catch(() => null);
    field("index").value = overview?.next_index || "";
    field("statement").value = NEW_STATEMENT;
    updatePreview();
    field("title").focus();
    return;
  }
  try {
    problem = await api.admin.problem(slug);
    fill(problem);
    field("slug").readOnly = true;
    renderStatus();
    initValidateEditor();
  } catch (error) {
    showError(describeError(error));
  }
}

start();
