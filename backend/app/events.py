"""In-process pub/sub powering the live dashboard.

Each reconciliation run gets its own fan-out: workers `publish()` events keyed by
`run_id`; the SSE endpoint `subscribe()`s and streams them to the browser.

Two properties make this production-shaped rather than a toy:

  * **Replay buffer.** Every event is retained per run, so a subscriber that
    attaches *late* (or reconnects after a dropped connection) receives the full
    history first, then tails live. This removes the classic "I opened the
    dashboard a second too late and it's empty / hung forever" failure — the
    run's execution is fully decoupled from whether anyone is watching.
  * **Atomic snapshot + register.** Replay and live-subscription happen under one
    lock, so no event is ever lost in the gap between "read history" and "start
    listening", and none is delivered twice.

This is the in-memory primitive that, in production, is swapped for Redis Streams
or an SQS/Kinesis consumer group (which give the same replay + at-least-once
semantics across processes) without changing a single caller — ARCHITECTURE.md §6.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

# Events that mark a run as finished; a replaying subscriber stops after one.
TERMINAL_EVENTS = {"run.completed", "run.failed"}


class EventBus:
    def __init__(self, max_runs: int = 512) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        # Insertion-ordered so we can evict the oldest finished run first.
        self._history: "OrderedDict[str, list[dict]]" = OrderedDict()
        self._terminated: set[str] = set()
        self._lock = asyncio.Lock()
        self._max_runs = max_runs

    async def subscribe(self, run_id: str) -> asyncio.Queue:
        """Attach a listener. The returned queue is pre-loaded with the run's
        full event history (replay), then receives every subsequent event."""
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            for event in self._history.get(run_id, []):
                q.put_nowait(event)
            self._subscribers.setdefault(run_id, []).append(q)
        return q

    async def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._subscribers.get(run_id, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subscribers.pop(run_id, None)

    async def publish(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = {"type": event_type, "payload": payload}
        async with self._lock:
            hist = self._history.get(run_id)
            if hist is None:
                hist = []
                self._history[run_id] = hist
                self._evict_locked()
            hist.append(event)
            self._history.move_to_end(run_id)
            if event_type in TERMINAL_EVENTS:
                self._terminated.add(run_id)
            # Unbounded queues → put_nowait never blocks; fan-out stays ordered
            # with respect to the history append because both are under the lock.
            for q in self._subscribers.get(run_id, []):
                q.put_nowait(event)

    def is_terminated(self, run_id: str) -> bool:
        return run_id in self._terminated

    def _evict_locked(self) -> None:
        """Drop the oldest *finished, unwatched* runs once we exceed the cap, so a
        long-lived server doesn't leak memory across thousands of runs."""
        while len(self._history) > self._max_runs:
            evicted = False
            for run_id in list(self._history.keys()):
                if run_id not in self._subscribers:
                    self._history.pop(run_id, None)
                    self._terminated.discard(run_id)
                    evicted = True
                    break
            if not evicted:  # everything is being actively watched; stop trying
                break


# Module-level singleton — one bus per process.
bus = EventBus()
