"""The console's event stream (GUI-9.1): `GET /api/events?topics=summary:nav,summary:alarms`, Server-Sent Events.

A page that shows counts used to poll `GET /api/summary/{page}` every few seconds, once per open tab. Here one background poller per BFF process
recomputes each page that at least one stream subscribed to, every `POLL_SECONDS`, through app/summary.py's own cached computation
(`app.state.summary_page`, so a page asked by tiles and pushed to streams is still one fan-out per cache period), and sends a stream an
`event: summary` only when a count it holds has changed. Every `PING_SECONDS` a stream gets an `event: ping`, which keeps every proxy on the way
(nginx reads with a 60 s timeout by default) from closing an idle connection, and at that moment the session is checked again, so a revoked
session or a deactivated user loses the stream within one ping.

Installed by app/main.py. Reads only. What a maintainer must know:

- A topic is `summary:<page>` with a page of `summary.PAGES`; nothing else is a topic yet (alarms, approvals and decisions as raw events would
  need the modules to publish them; the counts are what the console patches today). At most `MAX_TOPICS` per stream, at most
  `MAX_STREAMS_PER_USER` open streams per user in this process (several BFF instances each count their own).
- A stream ends by itself after `STREAM_MAX_SECONDS`; the browser's EventSource reconnects after `RETRY_MS`, which also re-reads the session.
  The tests shorten the three timings to read a whole stream with the buffering TestClient.
- What each stream has been sent is kept per stream (`_Subscriber.seen`), so "changed" is relative to what that browser holds, and a stream that
  opens later than another gets the full counts first, then only changes.
- The response carries `X-Accel-Buffering: no`, and smo/gui/nginx.conf has its own location for `/api/events` with buffering off: a buffering
  proxy would hold every event until its buffer fills.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import summary

log = logging.getLogger("smo-gui-bff")

POLL_SECONDS = 5.0
PING_SECONDS = 15.0
STREAM_MAX_SECONDS = 1800.0
START_GRACE_SECONDS = 30.0      # a stream reserved by the route but never started by the server (the client left at once) is dropped after this
RETRY_MS = 3000
MAX_TOPICS = 4
MAX_STREAMS_PER_USER = 5
TOPIC_PREFIX = "summary:"


def sse(event: str, data: dict) -> str:
    """One Server-Sent Event: the `event:` line, one `data:` line of compact JSON (it never holds a newline) and the blank line that ends it."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@dataclass(eq=False)
class _Subscriber:
    """One open stream: who, which summary pages, what it was last sent per page (`seen`) and what is waiting to be sent (`pending`)."""
    username: str
    pages: tuple[str, ...]
    created: float = field(default_factory=time.monotonic)
    started: bool = False
    seen: dict[str, dict] = field(default_factory=dict)
    pending: dict[str, dict] = field(default_factory=dict)
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def offer(self, body: dict) -> None:
        """Queue the counts of `body["page"]` for this stream if any differ from what it was last offered. A change not yet sent is merged with the
        new one (newest counts, union of the changed keys), so a slow browser gets one event per page, never a backlog."""
        page, counts = body["page"], body.get("counts") or {}
        before = self.seen.get(page)
        changed = [k for k, v in counts.items() if before is None or k not in before or before[k] != v]
        if not changed:
            return
        self.seen[page] = dict(counts)
        earlier = self.pending.get(page)
        if earlier is not None:
            changed = sorted(set(changed) | set(earlier["changed"]))
        self.pending[page] = {"page": page, "counts": dict(counts), "changed": changed, "computedAt": body.get("computedAt"),
                              "partial": body.get("partial", [])}
        self.wake.set()

    def drain(self) -> list[dict]:
        """The events waiting for this stream, in topic order, and an empty queue."""
        out = [self.pending.pop(p) for p in self.pages if p in self.pending]
        self.wake.clear()
        return out


class EventHub:
    """The streams of this process and the one poller that feeds them. The poller runs only while a stream is open: the first stream starts it,
    the last one to close cancels it."""

    def __init__(self, page_counts: Callable[[str], Awaitable[dict]]):
        self.page_counts = page_counts
        self.subscribers: set[_Subscriber] = set()
        self.task: asyncio.Task | None = None

    def open_streams(self, username: str) -> int:
        return sum(1 for s in self.subscribers if s.username == username)

    def add(self, sub: _Subscriber) -> None:
        self.subscribers.add(sub)

    def remove(self, sub: _Subscriber) -> None:
        """Forget `sub` (idempotent); with no stream left, stop the poller unless it is the caller."""
        self.subscribers.discard(sub)
        # The poller itself removing the last stream (a stale one) just lets its loop end; anyone else cancels it.
        if not self.subscribers and self.task is not None and not self.task.done() and asyncio.current_task() is not self.task:
            self.task.cancel()
            self.task = None

    def ensure_poller(self) -> None:
        """Start the poller on the running loop unless one is already running there (a test client may run each request on its own loop)."""
        loop = asyncio.get_running_loop()
        if self.task is None or self.task.done() or self.task.get_loop() is not loop:
            self.task = loop.create_task(self._run())

    async def _page(self, page: str) -> dict | None:
        """The summary body of `page` (app/summary.py's cached computation), or None when computing it raised. Never raises."""
        try:
            return await self.page_counts(page)
        except Exception:        # deliberate catch-all: one bad page must not stop the poller of every other stream
            log.exception("event stream: summary page %s failed", page)
            return None

    async def _run(self) -> None:
        """Every POLL_SECONDS: drop streams that never started, recompute each subscribed page once and offer it to its streams."""
        while self.subscribers:
            now = time.monotonic()
            for stale in [s for s in self.subscribers if not s.started and now - s.created > START_GRACE_SECONDS]:
                self.remove(stale)
            live = [s for s in self.subscribers if s.started]
            pages = sorted({p for s in live for p in s.pages})
            bodies = await asyncio.gather(*(self._page(p) for p in pages))
            for body in bodies:
                if body is None:
                    continue
                for s in live:
                    if body["page"] in s.pages:
                        s.offer(body)
            await asyncio.sleep(POLL_SECONDS)


def install(app: FastAPI, *, current_session: Callable, problem: Callable[..., JSONResponse]) -> None:
    """Add `GET /api/events` to `app`. Needs app/summary.py installed first (it reads `app.state.summary_page` when a stream opens)."""
    hub = EventHub(lambda page: app.state.summary_page(page))
    app.state.event_hub = hub

    def session_still_valid(request: Request) -> bool:
        """The same checks as when the stream opened (signature, user active, token version, logout): False once any fails."""
        try:
            current_session(request)
            return True
        except Exception:        # deliberate: whatever the check raises (its HTTP 401 / 403), the stream must end, not fail
            return False

    @app.get("/api/events", responses={200: {"description": "A `text/event-stream`: `event: summary` with "
                                                            "`{page, counts, changed, computedAt, partial}` when a count changed (the full counts first), "
                                                            "`event: ping` every 15 s", "content": {"text/event-stream": {}}}})
    async def events(request: Request, topics: str = Query(..., max_length=400, description="Comma-separated, at most 4: `summary:<page>` for a page "
                                                                                            "of `GET /api/summary/{page}`."),
                     session=Depends(current_session)):
        """Server-Sent Events of the summary counts of up to four console pages. 400 `UNKNOWN_TOPIC` / `TOO_MANY_TOPICS`, 403 when a count of a
        page needs a read the role does not have, 429 `TOO_MANY_STREAMS` past five open streams of one user."""
        wanted: list[str] = []
        for raw in topics.split(","):
            topic = raw.strip()
            if topic and topic not in wanted:
                wanted.append(topic)
        if not wanted:
            return problem(400, "UNKNOWN_TOPIC", "name at least one topic: summary:<page>")
        if len(wanted) > MAX_TOPICS:
            return problem(400, "TOO_MANY_TOPICS", f"at most {MAX_TOPICS} topics per stream")
        unknown = [t for t in wanted if not t.startswith(TOPIC_PREFIX) or t[len(TOPIC_PREFIX):] not in summary.PAGES]
        if unknown:
            return problem(400, "UNKNOWN_TOPIC", f"unknown topic {unknown[0]!r}: topics are summary:<page> with a page of "
                                                 f"{', '.join(sorted(summary.PAGES))}")
        pages = tuple(t[len(TOPIC_PREFIX):] for t in wanted)
        if not all(summary.page_allowed(p, session.user.role) for p in pages):
            return problem(403, "FORBIDDEN", "a count on one of these pages needs a read your role does not have")
        if hub.open_streams(session.user.username) >= MAX_STREAMS_PER_USER:
            return problem(429, "TOO_MANY_STREAMS", f"at most {MAX_STREAMS_PER_USER} open event streams per user: close another tab")
        # Reserved here, so the cap holds against streams opened at the same moment; the generator below always removes it, and the poller drops
        # one whose response never started (START_GRACE_SECONDS).
        sub = _Subscriber(session.user.username, pages)
        hub.add(sub)

        async def stream():
            """The stream body: the retry hint, the full counts of each page, then changes and pings until the deadline, a failed session check or
            the browser leaving (Starlette cancels the generator, and `finally` removes the stream)."""
            try:
                sub.started = True
                yield f"retry: {RETRY_MS}\n\n"
                for body in await asyncio.gather(*(hub._page(p) for p in pages)):
                    if body is not None:
                        sub.offer(body)
                hub.ensure_poller()
                now = time.monotonic()
                deadline, next_ping = now + STREAM_MAX_SECONDS, now + PING_SECONDS
                while True:
                    for event in sub.drain():
                        yield sse("summary", event)
                    now = time.monotonic()
                    if now >= deadline:
                        return
                    if now >= next_ping:
                        if not await run_in_threadpool(session_still_valid, request):
                            return
                        yield sse("ping", {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                        next_ping = now + PING_SECONDS
                    try:
                        await asyncio.wait_for(sub.wake.wait(), timeout=max(0.0, min(next_ping, deadline) - now))
                    except TimeoutError:
                        pass
            finally:
                hub.remove(sub)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
