// api.js - one small wrapper around fetch() for the JSON API.
//
// Every error from the server has the shape {"error": {"code", "message"}}.
// request() turns it into an ApiError, so callers can show a precise message
// (and the Retry-After seconds for "rate_limited" or "queue_full").

export class ApiError extends Error {
  constructor(status, code, message, retryAfter = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

async function request(method, path, body) {
  const options = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
  if (body instanceof FormData) {
    options.body = body;                 // the browser sets the multipart Content-Type
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the judge. Check that the server is running.");
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) {
    const err = (data && data.error) || {};
    const retry = Number(response.headers.get("Retry-After")) || null;
    throw new ApiError(response.status, err.code || "http_error",
      err.message || `Request failed (${response.status})`, retry);
  }
  return data;
}

export const api = {
  problems: () => request("GET", "/api/problems"),
  problem: (slug, contest) =>
    request("GET", `/api/problems/${encodeURIComponent(slug)}${contest ? `?contest=${encodeURIComponent(contest)}` : ""}`),
  contests: () => request("GET", "/api/contests"),
  contest: (slug) => request("GET", `/api/contests/${encodeURIComponent(slug)}`),
  standings: (slug) => request("GET", `/api/contests/${encodeURIComponent(slug)}/standings`),
  examStatus: (slug) => request("GET", `/api/contests/${encodeURIComponent(slug)}/exam-status`),
  examEvent: (slug, kind, detail = "") => request("POST", `/api/contests/${encodeURIComponent(slug)}/integrity`, { kind, detail }),
  submit: (problem, language, code, contest) =>
    request("POST", "/api/submit", contest ? { problem, language, code, contest } : { problem, language, code }),
  runSample: (problem, language, code) => request("POST", "/api/run-sample", { problem, language, code }),
  submissions: (limit = 50, scope = "mine") => request("GET", `/api/submissions?limit=${limit}&scope=${scope}`),
  submission: (id, withEvents = false) =>
    request("GET", `/api/submissions/${id}${withEvents ? "?events=1" : ""}`),
  sampleRun: (id) => request("POST", `/api/submissions/${id}/sample-run`),
  inspector: () => request("GET", "/api/inspector/state"),

  me: () => request("GET", "/api/auth/me"),
  login: (username, password) => request("POST", "/api/auth/login", { username, password }),
  signup: (username, password) => request("POST", "/api/auth/signup", { username, password }),
  logout: () => request("POST", "/api/auth/logout", {}),

  admin: {
    problems: () => request("GET", "/api/admin/problems"),
    problem: (slug) => request("GET", `/api/admin/problems/${encodeURIComponent(slug)}`),
    create: (data) => request("POST", "/api/admin/problems", data),
    update: (slug, data) => request("PUT", `/api/admin/problems/${encodeURIComponent(slug)}`, data),
    remove: (slug) => request("DELETE", `/api/admin/problems/${encodeURIComponent(slug)}`, { confirm: slug }),
    visibility: (slug, hidden) => request("POST", `/api/admin/problems/${encodeURIComponent(slug)}/visibility`, { hidden }),
    uploadTests(slug, file) {
      const form = new FormData();
      form.append("file", file);
      return request("POST", `/api/admin/problems/${encodeURIComponent(slug)}/tests`, form);
    },
    contests: () => request("GET", "/api/admin/contests"),
    contest: (slug) => request("GET", `/api/admin/contests/${encodeURIComponent(slug)}`),
    createContest: (data) => request("POST", "/api/admin/contests", data),
    updateContest: (slug, data) => request("PUT", `/api/admin/contests/${encodeURIComponent(slug)}`, data),
    contestVisibility: (slug, hidden) => request("POST", `/api/admin/contests/${encodeURIComponent(slug)}/visibility`, { hidden }),
    deleteContest: (slug) => request("DELETE", `/api/admin/contests/${encodeURIComponent(slug)}`, { confirm: slug }),
    unlock: (slug, username) => request("POST", `/api/admin/contests/${encodeURIComponent(slug)}/unlock`, { username }),
    user: (name) => request("GET", `/api/admin/users/${encodeURIComponent(name)}`),
    similarity: (problem, contest, threshold) => request("GET",
      `/api/admin/similarity?problem=${encodeURIComponent(problem)}&threshold=${threshold}${contest ? `&contest=${encodeURIComponent(contest)}` : ""}`),
    pair: (a, b) => request("GET", `/api/admin/similarity/pair?a=${a}&b=${b}`),
    validate: (slug, language, code) =>
      request("POST", `/api/admin/problems/${encodeURIComponent(slug)}/validate`, { language, code }),
  },
};

/** A human sentence for an ApiError, used in toasts. */
export function describeError(err) {
  if (!(err instanceof ApiError)) return "Something went wrong. Please try again.";
  if (err.code === "rate_limited") return `Slow down a little: try again in ${err.retryAfter || 60} s.`;
  if (err.code === "queue_full") return "The judge queue is full right now. Try again in a few seconds.";
  if (err.code === "login_required") return "Log in first: contests need an account.";
  return err.message;
}
