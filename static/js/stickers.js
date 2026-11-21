// stickers.js - the built-in comic stickers, one per verdict.
//
// All original artwork, drawn here in SVG (no image files, no copyright
// questions). The mascot is a little robot whose antenna splits in two like a
// process after fork(). Each verdict changes its face, its arms and the comic
// "burst" behind it. Animations are CSS classes (stk-*) in components.css, so
// "reduce motion" switches them off like everything else.

const INK = "#14201b";

/** A jagged comic burst: alternating outer/inner points around a center. */
function burst(cx, cy, outer, inner, points, seed) {
  let s = seed;
  const rand = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
  const out = [];
  for (let i = 0; i < points * 2; i++) {
    const angle = (Math.PI * i) / points - Math.PI / 2;
    const r = i % 2 === 0 ? outer * (0.86 + rand() * 0.18) : inner * (0.9 + rand() * 0.15);
    out.push(`${(cx + r * Math.cos(angle)).toFixed(1)},${(cy + r * Math.sin(angle)).toFixed(1)}`);
  }
  return out.join(" ");
}

const EYES = {
  happy: `<path d="M91 110 q7 -9 14 0 M115 110 q7 -9 14 0" fill="none" stroke="${INK}" stroke-width="4" stroke-linecap="round"/>`,
  confused: `<circle cx="98" cy="108" r="5" fill="${INK}"/><circle cx="122" cy="107" r="7.5" fill="#fff" stroke="${INK}" stroke-width="3"/>
    <circle cx="123" cy="108" r="3" fill="${INK}"/><path d="M114 95 q8 -6 16 -1" fill="none" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>`,
  sleep: `<path d="M91 108 q7 5 14 0 M115 108 q7 5 14 0" fill="none" stroke="${INK}" stroke-width="4" stroke-linecap="round"/>`,
  puffed: `<circle cx="98" cy="106" r="4" fill="${INK}"/><circle cx="122" cy="106" r="4" fill="${INK}"/>
    <circle cx="89" cy="117" r="5" fill="#ff8fa3" opacity="0.8"/><circle cx="131" cy="117" r="5" fill="#ff8fa3" opacity="0.8"/>`,
  dizzy: `<g class="stk-spin" style="transform-origin: 98px 107px"><path d="M98 107 m-6 0 a6 6 0 1 1 6 6 a4 4 0 1 1 -4 -4 a2 2 0 1 1 2 2" fill="none" stroke="${INK}" stroke-width="2.5"/></g>
    <g class="stk-spin" style="transform-origin: 122px 107px"><path d="M122 107 m-6 0 a6 6 0 1 1 6 6 a4 4 0 1 1 -4 -4 a2 2 0 1 1 2 2" fill="none" stroke="${INK}" stroke-width="2.5"/></g>`,
  worried: `<circle cx="98" cy="108" r="4.5" fill="${INK}"/><circle cx="122" cy="108" r="4.5" fill="${INK}"/>
    <path d="M90 99 l12 -4 M130 99 l-12 -4" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>`,
};

const MOUTHS = {
  grin: `<path d="M99 118 q11 12 22 0 z" fill="${INK}"/><path d="M104 123 q6 4 12 0" fill="#ff6b81"/>`,
  wavy: `<path d="M99 122 q4 -4 8 0 t8 0 t8 0" fill="none" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>`,
  snore: `<ellipse cx="110" cy="122" rx="4" ry="3" fill="${INK}"/>`,
  full: `<path d="M101 121 q9 5 18 0" fill="none" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>`,
  oh: `<ellipse cx="110" cy="122" rx="5.5" ry="6" fill="${INK}"/>`,
  flat: `<path d="M102 122 h16" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>`,
};

const ARMS = {
  up: `<path d="M70 104 L50 78 M150 104 L170 78" stroke="${INK}" stroke-width="5" stroke-linecap="round"/>
    <circle cx="48" cy="75" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/><circle cx="172" cy="75" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/>`,
  scratch: `<path d="M70 112 L56 128 M150 106 Q168 96 156 76" fill="none" stroke="${INK}" stroke-width="5" stroke-linecap="round"/>
    <circle cx="54" cy="131" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/><circle cx="154" cy="73" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/>`,
  down: `<path d="M70 112 L58 132 M150 112 L162 132" stroke="${INK}" stroke-width="5" stroke-linecap="round"/>
    <circle cx="57" cy="135" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/><circle cx="163" cy="135" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/>`,
  wide: `<path d="M66 112 L44 116 M154 112 L176 116" stroke="${INK}" stroke-width="5" stroke-linecap="round"/>
    <circle cx="41" cy="117" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/><circle cx="179" cy="117" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/>`,
  wrench: `<path d="M70 112 L58 132 M150 112 L166 98" stroke="${INK}" stroke-width="5" stroke-linecap="round"/>
    <circle cx="57" cy="135" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/>
    <g class="stk-wobble" style="transform-origin: 168px 96px"><path d="M166 98 L184 72" stroke="#9aa7b0" stroke-width="7" stroke-linecap="round"/>
      <path d="M178 66 a9 9 0 1 0 14 10 l-6 -2 l-3 -6 z" fill="#9aa7b0" stroke="${INK}" stroke-width="3" stroke-linejoin="round"/>
      <circle cx="168" cy="96" r="7" fill="#fff" stroke="${INK}" stroke-width="3"/></g>`,
};

/** The mascot. body: "normal" or "puffed" (memory limit). */
function mascot({ eyes, mouth, arms, body = "normal", color = "#F2589B" }) {
  const shape = body === "puffed"
    ? `<ellipse cx="110" cy="114" rx="50" ry="42" fill="${color}" stroke="${INK}" stroke-width="4"/>`
    : `<rect x="70" y="78" width="80" height="74" rx="20" fill="${color}" stroke="${INK}" stroke-width="4"/>`;
  return `
    <g class="stk-legs"><rect x="88" y="148" width="12" height="16" rx="4" fill="${color}" stroke="${INK}" stroke-width="3"/>
      <rect x="120" y="148" width="12" height="16" rx="4" fill="${color}" stroke="${INK}" stroke-width="3"/></g>
    <g class="stk-antenna"><path d="M110 80 V66 M110 66 L99 55 M110 66 L121 55" fill="none" stroke="${INK}" stroke-width="4" stroke-linecap="round"/>
      <circle cx="98" cy="53" r="5" fill="#ffd23f" stroke="${INK}" stroke-width="2.5"/><circle cx="122" cy="53" r="5" fill="#ffd23f" stroke="${INK}" stroke-width="2.5"/></g>
    ${ARMS[arms]}
    ${shape}
    <rect x="82" y="92" width="56" height="40" rx="11" fill="#FFF4F9" stroke="${INK}" stroke-width="3"/>
    ${EYES[eyes]}${MOUTHS[mouth]}`;
}

function word(text, color, size = 26) {
  return `<g class="stk-word"><text x="110" y="36" text-anchor="middle" transform="rotate(-6 110 30)"
    font-family="Inter, system-ui, sans-serif" font-weight="900" font-size="${size}" letter-spacing="0.5"
    fill="${color}" stroke="${INK}" stroke-width="7" paint-order="stroke" stroke-linejoin="round">${text}</text></g>`;
}

function frame(key, bg, ring, inner, describe) {
  return (content) => `<svg class="sticker" data-verdict="${key}" viewBox="0 0 220 200" width="220" height="200" role="img" aria-label="${describe}">
    <defs><pattern id="ht-${key}" width="8" height="8" patternUnits="userSpaceOnUse"><circle cx="4" cy="4" r="1.7" fill="${INK}" opacity="0.13"/></pattern></defs>
    <g class="stk-burst" style="transform-origin: 110px 112px">
      <polygon points="${burst(110, 112, 96, 70, 14, key.length * 7 + 3)}" fill="${bg}" stroke="${INK}" stroke-width="4" stroke-linejoin="round"/>
      <polygon points="${burst(110, 112, 96, 70, 14, key.length * 7 + 3)}" fill="url(#ht-${key})"/>
      <circle cx="110" cy="112" r="56" fill="${inner}" opacity="0.55"/>
      <circle cx="110" cy="112" r="64" fill="none" stroke="${ring}" stroke-width="3" stroke-dasharray="4 9" opacity="0.8"/>
    </g>
    <g class="stk-figure">${content}</g>
  </svg>`;
}

// ---------------------------------------------------------------- stickers
export const STICKERS = {
  accepted: {
    svg: frame("accepted", "#ffd23f", "#fff", "#fff3b0", "Comic sticker: the robot jumps with both arms up. ACCEPTED!")(`
      <g class="stk-jump">${mascot({ eyes: "happy", mouth: "grin", arms: "up" })}</g>
      <g class="stk-twinkle"><path d="M34 52 l4 10 l10 4 l-10 4 l-4 10 l-4 -10 l-10 -4 l10 -4 z" fill="#fff" stroke="${INK}" stroke-width="2.5"/>
        <path d="M186 140 l3 7 l7 3 l-7 3 l-3 7 l-3 -7 l-7 -3 l7 -3 z" fill="#fff" stroke="${INK}" stroke-width="2.5"/></g>
      ${word("ACCEPTED!", "#3ddc97")}`),
    captions: ["All tests passed. Flawless.", "fork() → exec() → victory.", "Exit code 0. Chef's kiss.", "The judge approves!"],
  },
  wrong_answer: {
    svg: frame("wrong_answer", "#ff8a5b", "#fff", "#ffd7c4", "Comic sticker: the robot scratches its head, confused. WRONG ANSWER!")(`
      <g class="stk-tilt" style="transform-origin: 110px 150px">${mascot({ eyes: "confused", mouth: "wavy", arms: "scratch" })}</g>
      <g class="stk-float"><text x="168" y="70" font-family="Inter, sans-serif" font-weight="900" font-size="30" fill="#fff" stroke="${INK}" stroke-width="5" paint-order="stroke">?</text>
        <text x="40" y="96" font-family="Inter, sans-serif" font-weight="900" font-size="22" fill="#fff" stroke="${INK}" stroke-width="5" paint-order="stroke">?</text></g>
      ${word("WRONG ANSWER", "#ff5d73", 23)}`),
    captions: ["Close… but the output disagrees.", "So close! Check the edge cases.", "The diff has opinions.", "Hmm. Did you read the output format?"],
  },
  time_limit: {
    svg: frame("time_limit", "#7cc6ff", "#fff", "#d8efff", "Comic sticker: the robot fell asleep next to a clock. TOO SLOW!")(`
      ${mascot({ eyes: "sleep", mouth: "snore", arms: "down", color: "#E3609E" })}
      <g class="stk-zzz"><text x="150" y="80" font-family="Inter, sans-serif" font-weight="900" font-size="20" fill="#fff" stroke="${INK}" stroke-width="4" paint-order="stroke">z</text>
        <text x="164" y="62" font-family="Inter, sans-serif" font-weight="900" font-size="26" fill="#fff" stroke="${INK}" stroke-width="4" paint-order="stroke">Z</text></g>
      <g class="stk-wobble" style="transform-origin: 42px 150px"><circle cx="42" cy="150" r="17" fill="#fff" stroke="${INK}" stroke-width="4"/>
        <path d="M42 150 V139 M42 150 L50 154" stroke="${INK}" stroke-width="3" stroke-linecap="round"/></g>
      ${word("TOO SLOW!", "#7cc6ff")}`),
    captions: ["SIGXCPU says: time's up.", "The watchdog got bored waiting.", "Maybe an O(n log n) would help?", "Zzz… the CPU limit ran out."],
  },
  memory_limit: {
    svg: frame("memory_limit", "#c59bff", "#fff", "#eadcff", "Comic sticker: the robot is puffed up like a balloon. TOO FULL!")(`
      <g class="stk-puff" style="transform-origin: 110px 114px">${mascot({ eyes: "puffed", mouth: "full", arms: "wide", body: "puffed" })}</g>
      <g class="stk-float"><rect x="152" y="56" width="34" height="18" rx="5" fill="#fff" stroke="${INK}" stroke-width="3"/>
        <text x="169" y="70" text-anchor="middle" font-family="JetBrains Mono, monospace" font-weight="800" font-size="11" fill="${INK}">+MB</text></g>
      ${word("TOO FULL!", "#c59bff")}`),
    captions: ["RLIMIT_AS said no more.", "That's a lot of bytes.", "Try freeing (or not allocating) some memory.", "The address space is full!"],
  },
  runtime_error: {
    svg: frame("runtime_error", "#ff6b6b", "#fff", "#ffd0d0", "Comic sticker: the robot is dizzy with smoke above it. CRASH!")(`
      <g class="stk-shake" style="transform-origin: 110px 150px">${mascot({ eyes: "dizzy", mouth: "oh", arms: "down", color: "#EA6AA6" })}</g>
      <g class="stk-smoke"><circle cx="150" cy="64" r="9" fill="#e6e6e6" stroke="${INK}" stroke-width="2.5"/><circle cx="162" cy="54" r="11" fill="#e6e6e6" stroke="${INK}" stroke-width="2.5"/>
        <circle cx="68" cy="66" r="8" fill="#e6e6e6" stroke="${INK}" stroke-width="2.5"/></g>
      ${word("CRASH!", "#ffd23f", 30)}`),
    captions: ["Killed by a signal. Ouch.", "Segfault? Division by zero? Check it.", "The kernel stepped in.", "Something went boom at runtime."],
  },
  compilation_error: {
    svg: frame("compilation_error", "#ffcf5c", "#fff", "#fff0c2", "Comic sticker: the robot holds a wrench, worried. WON'T BUILD!")(`
      ${mascot({ eyes: "worried", mouth: "flat", arms: "wrench" })}
      <path class="stk-drop" d="M146 86 q5 8 0 12 q-5 -4 0 -12 z" fill="#7cc6ff" stroke="${INK}" stroke-width="2"/>
      <g opacity="0.9"><path d="M20 176 h180" stroke="${INK}" stroke-width="10"/><path d="M20 176 h180" stroke="#ffd23f" stroke-width="6" stroke-dasharray="10 10"/></g>
      ${word("WON'T BUILD!", "#ffcf5c", 24)}`),
    captions: ["The compiler has notes for you.", "A missing semicolon, perhaps?", "Read the first error — it's usually the real one.", "No binary, no run."],
  },
};
