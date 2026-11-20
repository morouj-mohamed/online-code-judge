"""
judge/events.py - a thread-safe in-memory publish/subscribe broker.

Everything that happens in the judge becomes an event:
    type "judge"       a sandbox lifecycle event (fork, limits_applied, exec, ...)
    type "worker"      a worker changed state (idle / busy / stopped) or got a new child pid
    type "queue"       the queue length changed
    type "submission"  a submission changed status (Pending -> Running -> verdict)

Every event is a dict:
    {"seq": 17, "ts": 1700000000.5, "type": "judge", "submission_id": 4, "data": {...}}
`seq` is a global counter. A later phase will send it as the Server-Sent-Events
id, so a browser that reconnects can ask "everything after seq N".

Design rules
    * publish() never blocks and never waits for subscribers. Each subscriber has
      its own bounded buffer; when a slow subscriber's buffer is full, ITS oldest
      event is dropped (and counted in `dropped`). A stuck browser tab can
      therefore never slow down a worker.
    * Lock order is always broker lock -> subscriber lock, never the reverse
      (subscribers never call back into the broker while holding their own lock),
      so the two cannot deadlock.
    * publish() assigns the sequence number and delivers to all subscribers
      inside ONE critical section. So every subscriber sees events in sequence
      order, and subscribe(since_seq=...) can replay history and attach to the
      live stream with no gap and no duplicate.
"""

import collections
import threading
import time

import config


class Subscription:
    """One consumer's private buffer. Get events with get() or by iterating."""

    def __init__(self, broker, submission_id, types, maxlen):
        self._broker = broker
        self.submission_id = submission_id   # None = every event
        self.types = frozenset(types) if types else None   # None = every type
        self._maxlen = maxlen
        self._buffer = collections.deque()
        self._cv = threading.Condition(threading.Lock())
        self._closed = False
        self.dropped = 0          # events lost because this subscriber was too slow
        self.history_gap = False  # True if replay could not reach back far enough

    def _matches(self, event):
        if self.submission_id is not None and event["submission_id"] != self.submission_id:
            return False
        return self.types is None or event["type"] in self.types

    def _offer(self, event):
        """Called by the broker (while it holds its own lock). Never blocks."""
        with self._cv:
            if self._closed:
                return
            if len(self._buffer) >= self._maxlen:
                self._buffer.popleft()
                self.dropped += 1
            self._buffer.append(event)
            self._cv.notify()

    def get(self, timeout=None):
        """Next event, or None if `timeout` seconds pass (or the subscription is closed)."""
        with self._cv:
            if not self._buffer and not self._closed:
                self._cv.wait(timeout)
            return self._buffer.popleft() if self._buffer else None

    def drain(self):
        """Return everything buffered right now, without waiting."""
        with self._cv:
            items = list(self._buffer)
            self._buffer.clear()
            return items

    def __iter__(self):
        """Yield events until the subscription is closed."""
        while True:
            event = self.get(timeout=0.5)
            if event is not None:
                yield event
            elif self.closed:
                return

    @property
    def closed(self):
        with self._cv:
            return self._closed and not self._buffer

    def close(self):
        self._broker._unsubscribe(self)
        with self._cv:
            self._closed = True
            self._cv.notify_all()


class EventBroker:
    def __init__(self, history=None):
        self._lock = threading.Lock()
        self._subscribers = []
        self._seq = 0
        self._history = collections.deque(maxlen=history or config.EVENT_HISTORY)
        self._closed = False

    def publish(self, type, data=None, submission_id=None, ts=None):
        """Create an event, remember it, and hand it to every matching subscriber."""
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": ts if ts is not None else time.time(), "type": type,
                     "submission_id": submission_id, "data": data or {}}
            self._history.append(event)
            for sub in self._subscribers:
                if sub._matches(event):
                    sub._offer(event)
            return event

    def subscribe(self, submission_id=None, types=None, maxlen=None, since_seq=None):
        """
        Start receiving events.
          submission_id  only events of this submission (None = all events)
          types          only these event types (None = all)
          since_seq      first replay the remembered events with seq > since_seq,
                         then continue live (no gap, no duplicates)
        """
        sub = Subscription(self, submission_id, types, maxlen or config.SUBSCRIBER_BUFFER)
        with self._lock:
            if since_seq is not None:
                oldest = self._history[0]["seq"] if self._history else self._seq + 1
                sub.history_gap = oldest > since_seq + 1 and self._seq > since_seq
                for event in self._history:
                    if event["seq"] > since_seq and sub._matches(event):
                        sub._offer(event)
            self._subscribers.append(sub)
        return sub

    def _unsubscribe(self, sub):
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    def history(self, since_seq=0, submission_id=None):
        """The remembered events with seq > since_seq (oldest first)."""
        with self._lock:
            return [e for e in self._history if e["seq"] > since_seq
                    and (submission_id is None or e["submission_id"] == submission_id)]

    def stats(self):
        with self._lock:
            return {"published": self._seq, "subscribers": len(self._subscribers),
                    "remembered": len(self._history),
                    "dropped_total": sum(s.dropped for s in self._subscribers)}

    def close(self):
        """Close every subscription (their get()/iteration then ends)."""
        with self._lock:
            subs, self._subscribers = self._subscribers, []
            self._closed = True
        for sub in subs:
            sub.close()
