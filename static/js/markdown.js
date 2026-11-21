// markdown.js - a deliberately small Markdown renderer for problem statements.
//
// It supports exactly what the statements use: headings, paragraphs, bullet
// and numbered lists, fenced code blocks, `inline code`, **bold** and pictures
// on a line of their own:   ![The graph of sample 1](graph.png)
// All text is HTML-escaped FIRST, so a statement can never inject HTML or
// scripts. A picture may only name a file uploaded to this problem (no links
// to other sites), so a statement cannot load anything from outside.

import { escapeHtml } from "./format.js";

function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

const IMAGE_LINE = /^!\[([^\]]*)\]\(([a-z0-9][a-z0-9._-]{0,59}\.(?:png|jpg|gif|webp))\)$/;

/**
 * render(markdown, {skipTitle, skipSections, assetBase}) -> HTML string
 *   skipTitle     drop the first "# ..." heading (the page shows its own title)
 *   skipSections  "## Name" sections to leave out (shown as real UI instead)
 *   assetBase     where the problem's pictures are served, e.g. "/problems/sum-two/assets"
 */
export function renderMarkdown(source, { skipTitle = true, skipSections = [], assetBase = null } = {}) {
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
    const image = assetBase && IMAGE_LINE.exec(line.trim());
    if (image) {
      flushParagraph();
      flushList();
      const alt = escapeHtml(image[1].trim());
      out.push(`<figure class="statement-figure"><img src="${escapeHtml(`${assetBase}/${image[2]}`)}" alt="${alt}" loading="lazy">` +
        `${alt ? `<figcaption>${alt}</figcaption>` : ""}</figure>`);
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
