// Everything the code editor page needs from CodeMirror 6, bundled into ONE
// ES module (static/vendor/codemirror.js) so the site works offline and needs
// no build step. Rebuild with:  cd tools/vendor && npm install && npm run build
export { EditorState, Compartment } from "@codemirror/state";
export {
  EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter,
  drawSelection, dropCursor, rectangularSelection, crosshairCursor, highlightSpecialChars,
  placeholder,
} from "@codemirror/view";
export { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
export {
  bracketMatching, indentOnInput, syntaxHighlighting, HighlightStyle, foldGutter, foldKeymap,
  indentUnit,
} from "@codemirror/language";
export { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
export { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
export { tags } from "@lezer/highlight";
export { cpp } from "@codemirror/lang-cpp";
export { python } from "@codemirror/lang-python";
