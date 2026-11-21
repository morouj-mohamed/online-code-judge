// inspector-concepts.js - panel 8: the "OS concept" side drawer.
// The text and the exact source locations come from /api/inspector/concepts,
// which reads the line numbers from the running code (judge/concepts.py).

import { api } from "./api.js";
import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";

const PANEL_TITLES = {
  workers: "Worker pool", queue: "Submission queue", timeline: "Process lifecycle", limits: "Resource limits",
  isolation: "Isolation level", log: "Event log", stress: "Load & throughput",
};

export function initConcepts(drawer) {
  let catalog = null;
  let opener = null;

  async function open(panel, button) {
    opener = button;
    if (!catalog) {
      try {
        catalog = await api.request("GET", "/api/inspector/concepts");
      } catch {
        return;
      }
    }
    const ids = catalog.panels[panel] || [];
    const concepts = ids.map((id) => catalog.concepts.find((c) => c.id === id)).filter(Boolean);
    drawer.querySelector(".js-drawer-eyebrow").textContent = `${PANEL_TITLES[panel] || "Panel"} demonstrates`;
    drawer.querySelector(".js-drawer-title").textContent = concepts.map((c) => c.title).join(" · ");
    drawer.querySelector(".js-drawer-body").innerHTML = concepts.map((c) => `
      <section class="concept">
        <h3>${escapeHtml(c.title)}</h3>
        <p>${escapeHtml(c.text)}</p>
        <h4 class="eyebrow">Where in the code</h4>
        <ul class="sources">${c.sources.map((s) => `<li>
          <a class="mono" href="${escapeHtml(s.url || "#")}" target="_blank" rel="noopener">${escapeHtml(s.file)}:${s.line}</a>
          <code>${escapeHtml(s.function)}</code><span class="subtle">${escapeHtml(s.why)}</span></li>`).join("")}</ul>
      </section>`).join("");
    drawer.showModal();
    drawer.querySelector(".js-drawer-close").focus();
  }

  drawer.querySelector(".js-drawer-close").addEventListener("click", () => drawer.close());
  drawer.addEventListener("click", (event) => { if (event.target === drawer) drawer.close(); });
  drawer.addEventListener("close", () => opener?.focus());

  document.querySelectorAll(".js-concept").forEach((button) => {
    button.addEventListener("click", () => open(button.dataset.panel, button));
  });
  // Clicking a panel's title also opens its concept.
  document.querySelectorAll(".insp-panel h2").forEach((h) => {
    h.addEventListener("click", () => {
      const button = h.closest(".insp-panel").querySelector(".js-concept");
      open(button.dataset.panel, button);
    });
  });
  void icon;
}
