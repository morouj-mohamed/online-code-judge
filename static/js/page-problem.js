// page-problem.js - the workspace: statement on the left, editor on the right.
//
//   * resizable split pane (drag, or arrow keys on the divider), remembered
//   * CodeMirror editor, language switcher with starter templates, font size
//   * your code is saved as a draft per problem and language
//   * Run sample (Ctrl+Enter) shows the result inline, with a diff on WA
//   * Submit (Ctrl+Shift+Enter) queues the code and opens the live result page

import { api, describeError } from "./api.js";
import { announce, boot } from "./app.js";
import { renderDiff } from "./diff.js";
import { createEditor, starterCode } from "./editor.js";
import { escapeHtml, formatKb, formatMs, LANGUAGE_LABELS, verdictBadge, debounce } from "./format.js";
import { icon } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import {
  clearDraft, getDraft, getPref, getStatus, saveDraft, setPref, STATUS_META, recordVerdict,
} from "./store.js";
import { toast } from "./toast.js";

boot();

const workspace = document.getElementById("workspace");
const slug = workspace.dataset.slug;
const languageSelect = document.getElementById("language");
const runButton = document.querySelector(".js-run");
const submitButton = document.querySelector(".js-submit");
const runPanel = document.querySelector(".js-run-panel");
const saveState = document.querySelector(".js-save");
const fontValue = document.querySelector(".js-font-value");

let problem = null;
let editor = null;
let language = getPref("language", "cpp");
let fontSize = getPref("fontSize", 14);
let busy = false;

// ============================================================ statement ====
function renderStatus() {
  const status = getStatus(slug);
  const meta = STATUS_META[status];
  document.querySelector(".js-status").innerHTML =
    `<span class="status-label" data-status="${status}">${icon(meta.icon, { size: 15 })}${meta.label}</span>`;
}

function renderLimits() {
  const lim = problem.limits_by_language[language];
  document.querySelector(".js-limits").innerHTML = `
    <span class="chip" title="Time limit for ${LANGUAGE_LABELS[language]}">${icon("clock", { size: 13 })}
      Time <span class="num">${formatMs(lim.time_ms)}</span></span>
    <span class="chip" title="Memory limit for ${LANGUAGE_LABELS[language]}">${icon("memory", { size: 13 })}
      Memory <span class="num">${lim.memory_mb} MB</span></span>
    <span class="chip">${icon("list-checks", { size: 13 })}<span class="num">${problem.tests_count}</span> hidden tests</span>
    ${language === "python" ? `<span class="chip" title="Interpreted languages get more time and memory">${icon("info", { size: 13 })}Python limits are scaled up</span>` : ""}`;
}

function ioBlock(label, text, id) {
  return `<div class="io-block">
    <header><span id="${id}">${label}</span>
      <button class="btn btn-ghost btn-sm js-copy" type="button" data-copy="${escapeHtml(text)}"
        aria-describedby="${id}">${icon("copy", { size: 14 })}<span>Copy</span></button></header>
    <pre aria-labelledby="${id}">${escapeHtml(text)}</pre></div>`;
}

function renderStatement() {
  const article = document.querySelector(".js-statement");
  article.innerHTML = renderMarkdown(problem.statement, { skipSections: ["Example", "Examples", "Limits"] });
  article.setAttribute("aria-busy", "false");
  document.querySelector(".js-samples").innerHTML = `
    <h2 class="eyebrow">Sample</h2>
    <div class="sample-pair">
      ${ioBlock("Input", problem.sample_input, "sample-in")}
      ${ioBlock("Output", problem.sample_output, "sample-out")}
    </div>`;
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest(".js-copy");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
    const label = button.querySelector("span");
    const old = button.innerHTML;
    button.innerHTML = `${icon("check", { size: 14 })}<span>Copied</span>`;
    announce("Copied to clipboard");
    setTimeout(() => { button.innerHTML = old; }, 1400);
    void label;
  } catch {
    toast({ title: "Could not copy", message: "Select the text and copy it manually.", kind: "warning" });
  }
});

// =========================================================== split pane ====
function initSplitter() {
  const splitter = document.getElementById("splitter");
  const apply = (percent) => {
    const value = Math.min(75, Math.max(25, percent));
    workspace.style.setProperty("--split", `${value}%`);
    splitter.setAttribute("aria-valuenow", String(Math.round(value)));
    return value;
  };
  let current = apply(getPref("split", 46));

  splitter.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add("is-dragging");
    document.body.classList.add("is-resizing");
    const rect = workspace.getBoundingClientRect();
    const move = (e) => {
      current = apply(((e.clientX - rect.left) / rect.width) * 100);
    };
    const up = () => {
      splitter.classList.remove("is-dragging");
      document.body.classList.remove("is-resizing");
      splitter.removeEventListener("pointermove", move);
      setPref("split", current);
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", up, { once: true });
    splitter.addEventListener("pointercancel", up, { once: true });
  });
  splitter.addEventListener("keydown", (event) => {
    const steps = { ArrowLeft: -2, ArrowRight: 2, Home: -100, End: 100 };
    if (!(event.key in steps)) return;
    event.preventDefault();
    current = apply(current + steps[event.key]);
    setPref("split", current);
  });
  splitter.addEventListener("dblclick", () => {
    current = apply(46);
    setPref("split", current);
  });
}

// =============================================================== editor ====
const saveDraftSoon = debounce((code) => {
  saveDraft(slug, language, code);
  saveState.innerHTML = `${icon("cloud", { size: 13 })}Draft saved`;
}, 400);

function codeFor(lang) {
  const draft = getDraft(slug, lang);
  return typeof draft === "string" && draft.trim() ? draft : starterCode(lang);
}

async function initEditor() {
  languageSelect.value = language;
  fontValue.textContent = `${fontSize}px`;
  editor = await createEditor(document.getElementById("editor"), {
    doc: codeFor(language),
    language,
    fontSize,
    onChange: (code) => {
      saveState.textContent = "Editing…";
      saveDraftSoon(code);
    },
    onRun: () => runSample(),
    onSubmit: () => submit(),
  });
  if (editor.kind === "textarea") {
    toast({ title: "Simple editor in use", message: "The full editor could not load; your code still works.", kind: "warning" });
  }
}

languageSelect.addEventListener("change", () => {
  skipLanguageCheck = false;
  saveDraft(slug, language, editor.getValue());          // keep the old language's code
  language = languageSelect.value;
  setPref("language", language);
  editor.setLanguage(language);
  editor.setValue(codeFor(language));
  renderLimits();
  saveState.textContent = "";
  announce(`Language: ${LANGUAGE_LABELS[language]}`);
});

function changeFont(delta) {
  fontSize = Math.min(22, Math.max(11, fontSize + delta));
  setPref("fontSize", fontSize);
  fontValue.textContent = `${fontSize}px`;
  editor?.setFontSize(fontSize);
}
document.querySelector(".js-font-down").addEventListener("click", () => changeFont(-1));
document.querySelector(".js-font-up").addEventListener("click", () => changeFont(1));

document.querySelector(".js-reset").addEventListener("click", () => {
  if (!window.confirm(`Replace your ${LANGUAGE_LABELS[language]} code with the starter template?`)) return;
  clearDraft(slug, language);
  editor.setValue(starterCode(language));
  saveState.textContent = "";
  editor.focus();
});

// ============================================================ run / submit ==
function setBusy(button, isBusy) {
  busy = isBusy;
  button.classList.toggle("is-busy", isBusy);
  button.setAttribute("aria-busy", String(isBusy));
  for (const b of [runButton, submitButton]) b.disabled = isBusy;
  const glyph = button.querySelector("svg");
  if (glyph) {
    glyph.outerHTML = isBusy ? icon("loader", { size: 14, cls: "spinner" })
      : icon(button === runButton ? "play" : "send", { size: 14 });
  }
}

/**
 * A quick guess of the language of a piece of code, from tell-tale lines.
 * Returns "python", "cpp", "c" or null when unsure. Only used to warn.
 */
export function guessLanguage(code) {
  const text = code.replace(/\/\*[\s\S]*?\*\//g, "");
  const hasInclude = /^\s*#\s*include\b/m.test(text);
  const cppHints = /\b(std::|cout|cin\s*>>|using\s+namespace|template\s*<)/.test(text) ||
    /#\s*include\s*<(iostream|bits\/stdc\+\+\.h|vector|string|algorithm|map|set)>/.test(text);
  const pyHints = /^\s*(def |import \w|from \w+ import|print\(|elif |for \w+ in )/m.test(text) ||
    /\binput\(\)/.test(text);
  if (pyHints && !hasInclude && !/;\s*$/m.test(text)) return "python";
  if (cppHints) return "cpp";
  if (hasInclude) return "c";
  return null;
}

function switchLanguageKeepingCode(target) {
  const code = editor.getValue();
  language = target;
  languageSelect.value = target;
  setPref("language", target);
  editor.setLanguage(target);
  saveDraft(slug, target, code);
  renderLimits();
  announce(`Language: ${LANGUAGE_LABELS[target]}`);
}

/** Warn (once per attempt) when the code clearly is not in the selected language. */
function languageLooksWrong(code, retry) {
  const guess = guessLanguage(code);
  const mismatch = guess && guess !== language && !(guess === "c" && language === "cpp");
  if (!mismatch || skipLanguageCheck) return false;
  toast({
    title: `This looks like ${LANGUAGE_LABELS[guess]}`,
    message: `The selected language is ${LANGUAGE_LABELS[language]}, so it would not compile or run correctly.`,
    kind: "warning",
    actions: [
      { label: `Switch to ${LANGUAGE_LABELS[guess]}`, primary: true, onClick: () => { switchLanguageKeepingCode(guess); retry(); } },
      { label: "Keep it", onClick: () => { skipLanguageCheck = true; retry(); } },
    ],
  });
  return true;
}
let skipLanguageCheck = false;

function codeOrWarn() {
  const code = editor?.getValue() ?? "";
  if (!code.trim()) {
    toast({ title: "Nothing to run", message: "Write some code first.", kind: "warning" });
    return null;
  }
  return code;
}

function renderRunResult(result) {
  const test = result.test || {};
  let body = "";
  if (result.verdict === "Compilation Error") {
    body = `<pre class="console" aria-label="Compiler output">${escapeHtml(result.compile_output || result.message)}</pre>`;
  } else if (result.verdict === "Wrong Answer") {
    body = renderDiff(result.expected_output, result.stdout).html;
  } else if (result.verdict === "Accepted") {
    body = `<p class="muted">Your output matches the sample. Submit to run the ${problem.tests_count} hidden tests.</p>`;
  } else {
    body = `<p>${escapeHtml(result.message)}</p>`;
    if (test.stderr) body += `<pre class="console" aria-label="Standard error">${escapeHtml(test.stderr)}</pre>`;
  }
  runPanel.innerHTML = `
    <div class="run-panel-head">
      ${verdictBadge(result.verdict)}
      <span class="chip">${icon("clock", { size: 13 })}<span class="num">${formatMs(result.time_ms)}</span></span>
      <span class="chip">${icon("memory", { size: 13 })}<span class="num">${formatKb(result.memory_kb)}</span></span>
      <span class="spacer"></span>
      <span class="subtle" style="font-size: var(--text-xs)">Sample only</span>
      <button class="btn btn-ghost btn-sm btn-icon js-close-run" type="button" aria-label="Close sample result">${icon("x", { size: 14 })}</button>
    </div>
    <div class="run-panel-body">${body}</div>`;
  runPanel.hidden = false;
  runPanel.querySelector(".js-close-run").addEventListener("click", () => {
    runPanel.hidden = true;
    editor.focus();
  });
  announce(`Sample run: ${result.verdict}`);
}

async function runSample() {
  if (busy) return;
  const code = codeOrWarn();
  if (code === null || languageLooksWrong(code, runSample)) return;
  setBusy(runButton, true);
  try {
    const result = await api.runSample(slug, language, code);
    renderRunResult(result);
  } catch (error) {
    toast({ title: "Run failed", message: describeError(error), kind: "error" });
  } finally {
    setBusy(runButton, false);
  }
}

async function submit() {
  if (busy) return;
  const code = codeOrWarn();
  if (code === null || languageLooksWrong(code, submit)) return;
  setBusy(submitButton, true);
  saveDraft(slug, language, code);
  try {
    const created = await api.submit(slug, language, code);
    recordVerdict(slug, "Pending");            // at least "attempted" from now on
    window.location.href = `/submissions/${created.id}?since=${created.seq}`;
  } catch (error) {
    toast({ title: "Submission refused", message: describeError(error), kind: "error" });
    setBusy(submitButton, false);
  }
}

runButton.addEventListener("click", runSample);
submitButton.addEventListener("click", submit);

// Shortcuts when the focus is NOT in the editor (the editor handles its own and
// marks the event as handled with preventDefault, so it never runs twice).
document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || !(event.ctrlKey || event.metaKey) || event.key !== "Enter") return;
  event.preventDefault();
  if (event.shiftKey) submit();
  else runSample();
});

// ================================================================= start ====
async function load() {
  initSplitter();
  renderStatus();
  const editorReady = initEditor();
  try {
    problem = await api.problem(slug);
    renderStatement();
    renderLimits();
  } catch (error) {
    document.querySelector(".js-statement").innerHTML = `<div class="state" data-kind="error" role="alert">
      <span class="state-icon">${icon("offline", { size: 22 })}</span>
      <h3>Could not load the statement</h3><p>${escapeHtml(describeError(error))}</p>
      <button class="btn js-retry" type="button">${icon("refresh")}Try again</button></div>`;
    document.querySelector(".js-retry").addEventListener("click", () => location.reload());
  }
  await editorReady;
}

load();
