// store.js - what the browser remembers in localStorage:
//   * per-problem status: "solved" | "attempted" (absent = unsolved)
//   * code drafts per problem and language
//   * small preferences (language, editor font size, split position)
//
// The status is ALSO reconciled with the server's submission history
// (reconcileStatuses), so it can never claim something the judge did not say.

const STATUS_KEY = "ocj-status";
const PREFS_KEY = "ocj-prefs";

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or disabled: the app still works, it just forgets */
  }
}

export function getStatuses() {
  return read(STATUS_KEY, {});
}

export function getStatus(slug) {
  return getStatuses()[slug] || "unsolved";
}

/** Record a final verdict. "solved" is never downgraded. */
export function recordVerdict(slug, verdict) {
  const all = getStatuses();
  if (verdict === "Accepted") all[slug] = "solved";
  else if (all[slug] !== "solved") all[slug] = "attempted";
  write(STATUS_KEY, all);
}

/** Make localStorage agree with the server's list of finished submissions. */
export function reconcileStatuses(submissions) {
  const all = getStatuses();
  for (const sub of submissions) {
    if (sub.status === "Accepted") all[sub.problem] = "solved";
    else if (["Pending", "Running"].includes(sub.status)) continue;
    else if (all[sub.problem] !== "solved") all[sub.problem] = "attempted";
  }
  write(STATUS_KEY, all);
  return all;
}

export const STATUS_META = {
  solved: { label: "Solved", icon: "circle-check" },
  attempted: { label: "Attempted", icon: "circle-half" },
  unsolved: { label: "Not attempted", icon: "circle" },
};

export function getDraft(slug, language) {
  return read(`ocj-draft:${slug}:${language}`, null);
}

export function saveDraft(slug, language, code) {
  write(`ocj-draft:${slug}:${language}`, code);
}

export function clearDraft(slug, language) {
  try {
    localStorage.removeItem(`ocj-draft:${slug}:${language}`);
  } catch {
    /* ignore */
  }
}

export function getPref(name, fallback) {
  const prefs = read(PREFS_KEY, {});
  return name in prefs ? prefs[name] : fallback;
}

export function setPref(name, value) {
  const prefs = read(PREFS_KEY, {});
  prefs[name] = value;
  write(PREFS_KEY, prefs);
}
