// diff.js - side-by-side "expected vs your output" view.
//
// It compares exactly like the judge (judge/compare.py): line by line, with
// trailing whitespace ignored and empty lines at the very end ignored. Lines
// that differ are highlighted, and inside a line the text from the first
// different character onward is marked.

import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";

const MAX_LINES = 400;

function normalize(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n").map((l) => l.replace(/\s+$/, ""));
  while (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

function firstDifference(a, b) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return i;
}

function lineHtml(number, text, other, isDiff, missingLabel) {
  if (text === undefined) {
    return `<div class="diff-line is-diff"><span class="ln">${number}</span><span class="tx eol">${missingLabel}</span></div>`;
  }
  let body = escapeHtml(text);
  if (isDiff && other !== undefined) {
    const at = firstDifference(text, other);
    body = `${escapeHtml(text.slice(0, at))}<mark>${escapeHtml(text.slice(at)) || " "}</mark>`;
  }
  if (!text) body = '<span class="eol">(empty line)</span>';
  return `<div class="diff-line${isDiff ? " is-diff" : ""}"><span class="ln">${number}</span><span class="tx">${body}</span></div>`;
}

/** Returns {html, same, firstLine}. */
export function renderDiff(expected, actual, { actualLabel = "Your output" } = {}) {
  const exp = normalize(expected);
  const act = normalize(actual);
  const total = Math.min(Math.max(exp.length, act.length), MAX_LINES);
  let firstLine = null;
  const left = [];
  const right = [];
  for (let i = 0; i < total; i++) {
    const isDiff = exp[i] !== act[i];
    if (isDiff && firstLine === null) firstLine = i + 1;
    left.push(lineHtml(i + 1, exp[i], act[i], isDiff, "(no line here)"));
    right.push(lineHtml(i + 1, act[i], exp[i], isDiff, "(missing line)"));
  }
  const same = firstLine === null && exp.length === act.length;
  const note = Math.max(exp.length, act.length) > MAX_LINES
    ? `<p class="subtle">Showing the first ${MAX_LINES} lines.</p>` : "";
  const html = `<div class="diff" role="group" aria-label="Expected output compared with ${escapeHtml(actualLabel.toLowerCase())}">
      <div class="diff-col is-expected">
        <header>${icon("circle-check", { size: 14 })}Expected output</header>
        <pre class="diff-lines">${left.join("") || '<div class="diff-line"><span class="ln">1</span><span class="tx eol">(empty)</span></div>'}</pre>
      </div>
      <div class="diff-col">
        <header>${icon("terminal", { size: 14 })}${escapeHtml(actualLabel)}</header>
        <pre class="diff-lines">${right.join("") || '<div class="diff-line"><span class="ln">1</span><span class="tx eol">(no output)</span></div>'}</pre>
      </div>
    </div>${note}`;
  return { html, same, firstLine };
}
