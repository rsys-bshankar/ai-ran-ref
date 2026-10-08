"""A short-lived cache of SME's token introspection answers (PR-SEC-5.4).

R1 Termination asks SME whether the bearer token is active on every proxied request (`main._introspect_token`). With `R1_INTROSPECTION_CACHE_SECONDS` above 0 it
remembers an answer for that many seconds, so a burst of calls with one token costs SME one lookup. Off (0) by default: then nothing here is ever consulted and the
gateway behaves exactly as before.

What the cache promises, and what it does not:

  - A token SME said was active is honoured from the cache for at most the TTL, and never past the token's own `exp`. A token revoked at SME (its invoker offboarded) is
    therefore honoured for at most the TTL after the revocation, by any replica of the gateway.
  - A revocation made through this gateway (`DELETE /sme/invoker-registrations/{id}`, a real `purge-stale`) evicts the entries of that invoker (or all of them) at once, in
    this process, so *this* replica stops honouring the token on the next request. Other replicas do not hear about it: their bound is the TTL.
  - A token SME said was NOT active is remembered for `min(TTL, NEGATIVE_SECONDS)`: enough to take a flood of wrong tokens off SME, short enough that nothing waits long.
  - Only SME's answer is cached. "SME did not answer" (a transport error or a 5xx) is never stored, so an outage is a 503 as before, and a stored answer is never served
    past its TTL to ride one out.
  - The key is the SHA-256 of the token: the raw token is never held. The size is bounded; the oldest entry goes first when it is full.
  - A change to the role SME records for an invoker is seen after at most the TTL, like a revocation that does not go through this gateway.

The lookup-then-store race with a revocation is closed by a generation number: an answer fetched while a revocation was evicting is not stored.
"""

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import NamedTuple

NEGATIVE_SECONDS = 5.0
DEFAULT_MAX_ENTRIES = 10000

Answer = tuple[str, str] | None          # (invoker id, role) of an active token, None for an inactive one


class _Entry(NamedTuple):
    expires: float
    answer: Answer


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class IntrospectionCache:
    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_entries = max(1, max_entries)
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self.generation = 0

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, token: str) -> tuple[bool, Answer]:
        """(True, answer) for a live entry, (False, None) otherwise. An expired entry is dropped on the way."""
        key = token_key(token)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False, None
            if entry.expires <= self._clock():
                del self._entries[key]
                return False, None
            return True, entry.answer

    def put(self, token: str, answer: Answer, ttl: float, generation: int) -> bool:
        """Remember `answer` for `ttl` seconds (a positive answer for at most the token's life: the caller passes the smaller). Not stored when a revocation evicted since
        `generation` was read (the answer may be from before it), or when `ttl` is not positive."""
        if ttl <= 0:
            return False
        key = token_key(token)
        with self._lock:
            if generation != self.generation:
                return False
            self._entries.pop(key, None)
            self._entries[key] = _Entry(self._clock() + ttl, answer)
            if len(self._entries) > self.max_entries:
                self._drop_expired()
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
            return True

    def _drop_expired(self) -> None:
        now = self._clock()
        for key in [k for k, e in self._entries.items() if e.expires <= now]:
            del self._entries[key]

    def evict_invoker(self, invoker_id: str) -> int:
        """A revocation of `invoker_id`: drop every entry for it (and refuse to store an answer that was in flight)."""
        with self._lock:
            self.generation += 1
            gone = [k for k, e in self._entries.items() if e.answer is not None and e.answer[0] == invoker_id]
            for key in gone:
                del self._entries[key]
            return len(gone)

    def clear(self) -> int:
        with self._lock:
            self.generation += 1
            count = len(self._entries)
            self._entries.clear()
            return count
