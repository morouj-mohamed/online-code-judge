// sse.js - a thin layer over EventSource (Server-Sent Events).
//
// SSE is a long-lived HTTP response in which the server writes text messages:
//     id: 42 / event: test / data: {...}
// The browser parses them and fires one DOM event per message. If the
// connection drops, EventSource reconnects by itself and sends the last id it
// saw (Last-Event-ID), so the server can replay what was missed.
//
// openStream({submission, since, types}, {test: fn, submission: fn, ...}, onState)
// returns {close()}. onState receives "connecting" | "open" | "reconnecting" | "closed".

const EVENT_TYPES = ["hello", "snapshot", "submission", "test", "judge", "worker", "queue", "end"];

export function openStream(params = {}, handlers = {}, onState = () => {}) {
  const query = new URLSearchParams();
  if (params.submission != null) query.set("submission", params.submission);
  if (params.since != null) query.set("since", params.since);
  if (params.types) query.set("types", params.types.join(","));
  if (params.scope) query.set("scope", params.scope);
  const url = `/api/stream${query.toString() ? `?${query}` : ""}`;

  let closed = false;
  const source = new EventSource(url, { withCredentials: true });
  onState("connecting");

  source.addEventListener("open", () => onState("open"));
  source.addEventListener("error", () => {
    if (closed) return;
    // CONNECTING = the browser is retrying on its own; CLOSED = it gave up.
    onState(source.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
  });

  for (const type of EVENT_TYPES) {
    source.addEventListener(type, (message) => {
      let data = null;
      try {
        data = JSON.parse(message.data);
      } catch {
        return;
      }
      if (type === "end") {
        // The server finished this stream. Close it, or EventSource would
        // reconnect and start a new one.
        closed = true;
        source.close();
        onState("closed");
      }
      if (handlers[type]) handlers[type](data, message.lastEventId);
    });
  }

  return {
    close() {
      closed = true;
      source.close();
    },
  };
}
