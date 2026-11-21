// markdown.js - a deliberately small Markdown renderer for problem statements.
//
// It supports exactly what the statements use: headings, paragraphs, bullet
// and numbered lists, fenced code blocks, `inline code` and **bold**. All text
// is HTML-escaped FIRST, so a statement can never inject HTML or scripts.

import { escapeHtml } from "./format.js";

function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

/**
 * render(markdown, {skipTitle, skipSections}) -> HTML string
 *   skipTitle     drop the first "# ..." heading (the page shows its own title)
 *   skipSections  "## Name" sections to leave out (shown as real UI instead)
 */
export function renderMarkdown(source, { skipTitle = true, skipSections = [] } = {}) {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const skip = new Set(skipSections.map((s) => s.toLowerCase()));
  const out = [];
  let paragraph = [];
  let list = null;
  let skipping = false;
  let titleDone = !skipTitle;

  const flushParagraph = () => {
    if (paragraph.length) out.push(`<p>${inline(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`);
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      if (level === 1 && !titleDone) {
        titleDone = true;
        continue;
      }
      skipping = level === 2 && skip.has(heading[2].trim().toLowerCase());
      if (!skipping) out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (skipping) continue;

    if (line.startsWith("```")) {
      flushParagraph();
      flushList();
      const code = [];
      while (++i < lines.length && !lines[i].startsWith("```")) code.push(lines[i]);
      out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tag !== tag) {
        flushList();
        list = { tag, items: [] };
      }
      list.items.push((bullet || numbered)[1]);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushList();
  return out.join("\n");
}
