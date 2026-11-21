"""
judge/queue.py - a bounded producer-consumer queue, built by hand.

We do NOT use the standard library's queue.Queue. This is the classic textbook
solution with one lock and two counting semaphores:

    items         a deque (the buffer)                    - protected by `mutex`
    empty_slots   Semaphore(capacity)  "how many free places are left"
    full_slots    Semaphore(0)         "how many items are waiting"
    mutex         Lock                 "only one thread touches the deque"

    put(x):  empty_slots.acquire()   # wait for a free place (blocks when full)
             with mutex: append(x)
             full_slots.release()    # announce one more item

    get():   full_slots.acquire()    # wait for an item (blocks when empty)
             with mutex: popleft()
             empty_slots.release()   # announce one more free place

Why this is correct
-------------------
RACE CONDITIONS
    Two threads must never change the deque at the same time, so every touch of
    the deque happens inside `with mutex`. The semaphores do the counting, so
    the queue can never hold more than `capacity` items (a producer cannot get
    past empty_slots.acquire() when it is full) and a consumer can never pop
    from an empty deque (full_slots is released only AFTER the append, so a
    consumer that got a permit always finds an item).

DEADLOCK
    A deadlock needs a cycle of threads waiting for each other. Here
    (1) the semaphore is always acquired BEFORE the mutex, in both put and get,
        so every thread takes the two in the same order;
    (2) the mutex is never held while waiting for a semaphore, only for one
        quick deque operation, so whoever owns it always finishes;
    (3) no thread ever holds two locks at once.
    (If put took the mutex first and then waited for a free place, a producer
    would sleep holding the mutex while the consumers, who also need it to free
    a place, could never run: that is the classic deadlock we avoid.)

STARVATION
    Items leave in the order they arrived (a deque used as a FIFO), so no item
    waits forever behind newer ones. Threads blocked on a semaphore are woken
    in the order they started waiting (the threading module keeps waiters in a
    FIFO list), so no producer and no consumer is overtaken forever. Each call
    waits ONCE (no retry loop), so a waiter never loses its place in line.

SHUTDOWN
    close() refuses new items but lets consumers drain what is already queued.
    Blocked threads must be woken up, or they would sleep forever. We count the
    threads that are blocked and release the semaphores that many times.
"""

import collections
import threading


class Full(Exception):
    """put() could not add the item (queue full, or timeout)."""


class Empty(Exception):
    """get() found nothing (queue empty, or timeout)."""


class Closed(Exception):
    """The queue is closed: put() is refused, and get() has nothing left to drain."""


class BoundedQueue:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self._items = collections.deque()
        self._mutex = threading.Lock()
        self._empty_slots = threading.Semaphore(capacity)
        self._full_slots = threading.Semaphore(0)
        self._closed = False
        # How many threads are blocked inside put() / get() right now.
        # Protected by `_mutex`. Needed only to wake them up in close().
        self._waiting_puts = 0
        self._waiting_gets = 0
        # How many items ever went in / came out (for the OS Inspector page).
        # Protected by `_mutex` like the deque itself.
        self.produced = 0
        self.consumed = 0

    # ------------------------------------------------------------------ producer
    def put(self, item, block=True, timeout=None):
        """
        Add an item. If the queue is full: wait (block=True, up to `timeout`
        seconds) or raise Full (block=False). Raises Closed after close().
        """
        # Step 1: get permission for one free place.
        with self._mutex:
            if self._closed:
                raise Closed("queue is closed")
            self._waiting_puts += 1
        try:
            got = self._empty_slots.acquire(blocking=block, timeout=timeout if block else None)
        finally:
            with self._mutex:
                self._waiting_puts -= 1
        if not got:
            raise Full("queue is full")

        # Step 2: we own one free place. Put the item in, under the mutex.
        with self._mutex:
            if self._closed:
                # We were woken up by close(), or close() ran in between.
                # Give the place back and refuse: nothing may enter after close,
                # otherwise an item could stay in the queue with nobody to take it.
                self._empty_slots.release()
                raise Closed("queue is closed")
            self._items.append(item)
            self.produced += 1
        # Step 3: announce the item. Only now can a consumer take it.
        self._full_slots.release()

    def put_nowait(self, item):
        self.put(item, block=False)

    # ------------------------------------------------------------------ consumer
    def get(self, block=True, timeout=None):
        """
        Remove and return the oldest item. If the queue is empty: wait
        (block=True, up to `timeout` seconds) or raise Empty (block=False).
        Raises Closed when the queue is closed AND empty (nothing left to drain).
        """
        with self._mutex:
            if self._closed and not self._items:
                raise Closed("queue is closed and empty")
            self._waiting_gets += 1
        try:
            got = self._full_slots.acquire(blocking=block, timeout=timeout if block else None)
        finally:
            with self._mutex:
                self._waiting_gets -= 1
        if not got:
            raise Empty("queue is empty")

        with self._mutex:
            if not self._items:
                # A permit with no item: it came from close() waking us up.
                raise Closed("queue is closed and empty")
            item = self._items.popleft()
            self.consumed += 1
        self._empty_slots.release()   # one more free place
        return item

    def get_nowait(self):
        return self.get(block=False)

    # ----------------------------------------------------------------- lifecycle
    def close(self):
        """Refuse new items; let consumers drain; wake every blocked thread."""
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            wake_consumers = self._waiting_gets
            wake_producers = self._waiting_puts
        # Extra permits make the blocked threads return from acquire(); they
        # then see `_closed` (and an empty deque for consumers) and raise Closed.
        for _ in range(wake_consumers):
            self._full_slots.release()
        for _ in range(wake_producers):
            self._empty_slots.release()

    # ------------------------------------------------------------------- inspect
    def snapshot(self):
        """A consistent picture: items in FIFO order (oldest first) and the counters."""
        with self._mutex:
            return {"items": list(self._items), "length": len(self._items), "capacity": self.capacity,
                    "produced": self.produced, "consumed": self.consumed}

    def qsize(self):
        with self._mutex:
            return len(self._items)

    @property
    def closed(self):
        with self._mutex:
            return self._closed
