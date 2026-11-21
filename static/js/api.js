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
  if (body !== undefined) {
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
  problem: (slug) => request("GET", `/api/problems/${encodeURIComponent(slug)}`),
  submit: (problem, language, code) => request("POST", "/api/submit", { problem, language, code }),
  runSample: (problem, language, code) => request("POST", "/api/run-sample", { problem, language, code }),
  submissions: (limit = 50) => request("GET", `/api/submissions?limit=${limit}`),
  submission: (id, withEvents = false) =>
    request("GET", `/api/submissions/${id}${withEvents ? "?events=1" : ""}`),
  sampleRun: (id) => request("POST", `/api/submissions/${id}/sample-run`),
  inspector: () => request("GET", "/api/inspector/state"),
};

/** A human sentence for an ApiError, used in toasts. */
export function describeError(err) {
  if (!(err instanceof ApiError)) return "Something went wrong. Please try again.";
  if (err.code === "rate_limited") return `Slow down a little: try again in ${err.retryAfter || 60} s.`;
  if (err.code === "queue_full") return "The judge queue is full right now. Try again in a few seconds.";
  return err.message;
}
