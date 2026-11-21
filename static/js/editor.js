// editor.js - the code editor.
//
// Uses CodeMirror 6 from static/vendor/codemirror.js (bundled locally, so it
// works offline). The theme is written with CSS variables from tokens.css, so
// it switches between light and dark together with the rest of the page.
// If the bundle cannot load for any reason, a plain <textarea> with a
// line-number gutter takes over: the page keeps working.
//
// createEditor(host, options) -> Promise<{getValue, setValue, setLanguage,
//                                         setFontSize, focus, kind}>

const STARTERS = {
  c: `#include <stdio.h>

int main(void) {
    // Read the input from stdin, print the answer to stdout.

    return 0;
}
`,
  cpp: `#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    // Read the input from stdin, print the answer to stdout.

    return 0;
}
`,
  python: `import sys


def main():
    data = sys.stdin.read().split()
    # Read the input from stdin, print the answer to stdout.


main()
`,
};

export function starterCode(language) {
  return STARTERS[language] || "";
}

async function loadCodeMirror() {
  return import("/static/vendor/codemirror.js");
}

function buildTheme(cm) {
  const { EditorView, HighlightStyle, syntaxHighlighting, tags: t } = cm;
  const theme = EditorView.theme({
    "&": {
      height: "100%",
      backgroundColor: "var(--code-bg)",
      color: "var(--text)",
      fontSize: "var(--editor-font, 14px)",
    },
    ".cm-scroller": {
      fontFamily: "var(--font-mono)",
      lineHeight: "1.65",
      fontVariantLigatures: "contextual",
    },
    ".cm-content": { padding: "12px 0", caretColor: "var(--code-cursor)" },
    ".cm-line": { padding: "0 16px 0 12px" },
    ".cm-gutters": {
      backgroundColor: "var(--code-gutter)",
      color: "var(--code-gutter-text)",
      border: "none",
      borderRight: "1px solid var(--border)",
    },
    ".cm-lineNumbers .cm-gutterElement": { padding: "0 10px 0 14px", minWidth: "40px" },
    ".cm-activeLine": { backgroundColor: "var(--code-active-line)" },
    ".cm-activeLineGutter": { backgroundColor: "var(--code-active-line)", color: "var(--text)" },
    "&.cm-focused .cm-cursor": { borderLeftColor: "var(--code-cursor)", borderLeftWidth: "2px" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      backgroundColor: "var(--selection) !important",
    },
    ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": {
      backgroundColor: "var(--accent-soft)",
      outline: "1px solid color-mix(in srgb, var(--accent) 55%, transparent)",
      borderRadius: "2px",
      color: "inherit",
    },
    ".cm-nonmatchingBracket": { color: "var(--v-wa)" },
    ".cm-selectionMatch": { backgroundColor: "var(--accent-softer)" },
    ".cm-foldGutter .cm-gutterElement": { padding: "0 4px", color: "var(--text-subtle)" },
    ".cm-placeholder": { color: "var(--text-subtle)" },
    ".cm-panels": { backgroundColor: "var(--surface-2)", color: "var(--text)" },
    ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
    ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--v-tle) 25%, transparent)" },
    ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "var(--accent-soft)" },
    ".cm-textfield, .cm-button": {
      backgroundColor: "var(--surface-1)", color: "var(--text)",
      border: "1px solid var(--border-strong)", borderRadius: "6px", backgroundImage: "none",
    },
  });
  const highlight = HighlightStyle.define([
    { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword], color: "var(--code-keyword)", fontWeight: "600" },
    { tag: [t.typeName, t.className, t.standard(t.typeName)], color: "var(--code-type)" },
    { tag: [t.string, t.special(t.string), t.character], color: "var(--code-string)" },
    { tag: [t.number, t.bool, t.null, t.atom], color: "var(--code-number)" },
    { tag: [t.comment, t.lineComment, t.blockComment], color: "var(--code-comment)", fontStyle: "italic" },
    { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.function(t.variableName))], color: "var(--code-function)" },
    { tag: [t.operator, t.punctuation, t.bracket], color: "var(--code-operator)" },
    { tag: [t.processingInstruction, t.meta, t.macroName], color: "var(--code-meta)" },
    { tag: [t.self, t.special(t.variableName)], color: "var(--code-type)", fontStyle: "italic" },
    { tag: t.invalid, color: "var(--v-wa)" },
  ]);
  return [theme, syntaxHighlighting(highlight)];
}

function languageExtension(cm, language) {
  return language === "python" ? cm.python() : cm.cpp();
}

async function createCodeMirror(host, { doc, language, fontSize, onChange, onRun, onSubmit }) {
  const cm = await loadCodeMirror();
  const languageSlot = new cm.Compartment();
  host.style.setProperty("--editor-font", `${fontSize}px`);

  // Our shortcuts come first so they win over the default keymap
  // (which binds Ctrl+Enter to "insert blank line").
  const shortcuts = cm.keymap.of([
    { key: "Mod-Enter", run: () => { onRun(); return true; } },
    { key: "Mod-Shift-Enter", run: () => { onSubmit(); return true; } },
  ]);

  const state = cm.EditorState.create({
    doc,
    extensions: [
      shortcuts,
      cm.lineNumbers(),
      cm.highlightActiveLineGutter(),
      cm.highlightSpecialChars(),
      cm.history(),
      cm.foldGutter({ openText: "▾", closedText: "▸" }),
      cm.drawSelection(),
      cm.dropCursor(),
      cm.EditorState.allowMultipleSelections.of(true),
      cm.indentOnInput(),
      cm.indentUnit.of("    "),
      cm.bracketMatching(),
      cm.closeBrackets(),
      cm.rectangularSelection(),
      cm.crosshairCursor(),
      cm.highlightActiveLine(),
      cm.highlightSelectionMatches(),
      cm.keymap.of([
        ...cm.closeBracketsKeymap,
        ...cm.defaultKeymap,
        ...cm.searchKeymap,
        ...cm.historyKeymap,
        ...cm.foldKeymap,
        cm.indentWithTab,
      ]),
      languageSlot.of(languageExtension(cm, language)),
      buildTheme(cm),
      cm.EditorView.contentAttributes.of({ "aria-label": "Code editor", spellcheck: "false" }),
      cm.EditorView.updateListener.of((update) => {
        if (update.docChanged) onChange(update.state.doc.toString());
        if (update.focusChanged) host.classList.toggle("is-focused", update.view.hasFocus);
      }),
    ],
  });
  const view = new cm.EditorView({ state, parent: host });

  return {
    kind: "codemirror",
    getValue: () => view.state.doc.toString(),
    setValue(text) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
    },
    setLanguage(lang) {
      view.dispatch({ effects: languageSlot.reconfigure(languageExtension(cm, lang)) });
    },
    setFontSize(px) {
      host.style.setProperty("--editor-font", `${px}px`);
      view.requestMeasure();
    },
    focus: () => view.focus(),
  };
}

/** The fallback: a textarea with line numbers, Tab indentation and shortcuts. */
function createTextarea(host, { doc, fontSize, onChange, onRun, onSubmit }) {
  host.innerHTML = `<div class="fallback-editor"><div class="gutter" aria-hidden="true"></div>
    <textarea aria-label="Code editor" spellcheck="false" autocapitalize="off" autocomplete="off"></textarea></div>`;
  const area = host.querySelector("textarea");
  const gutter = host.querySelector(".gutter");
  const wrap = host.querySelector(".fallback-editor");
  const sync = () => {
    const lines = area.value.split("\n").length;
    gutter.textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n");
    area.style.height = "auto";
    area.style.height = `${area.scrollHeight}px`;
  };
  area.value = doc;
  host.style.setProperty("--editor-font", `${fontSize}px`);
  area.addEventListener("input", () => {
    sync();
    onChange(area.value);
  });
  area.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      if (event.shiftKey) onSubmit();
      else onRun();
    } else if (event.key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      area.setRangeText("    ", area.selectionStart, area.selectionEnd, "end");
      sync();
      onChange(area.value);
    }
  });
  area.addEventListener("focus", () => host.classList.add("is-focused"));
  area.addEventListener("blur", () => host.classList.remove("is-focused"));
  wrap.addEventListener("click", (event) => {
    if (event.target === wrap) area.focus();
  });
  sync();
  return {
    kind: "textarea",
    getValue: () => area.value,
    setValue(text) {
      area.value = text;
      sync();
    },
    setLanguage() {},
    setFontSize(px) {
      host.style.setProperty("--editor-font", `${px}px`);
      sync();
    },
    focus: () => area.focus(),
  };
}

export async function createEditor(host, options) {
  try {
    const editor = await createCodeMirror(host, options);
    host.querySelector(".editor-loading")?.remove();
    return editor;
  } catch (error) {
    console.warn("CodeMirror failed to load, using the plain editor instead.", error);
    return createTextarea(host, options);
  }
}
