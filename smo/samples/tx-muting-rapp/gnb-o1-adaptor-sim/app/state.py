"""In-memory state of the gNB O1 adaptor simulator: the running configuration, raised alarms, injected faults and the
event log that the CLI (and anything else) follows."""

import collections
import datetime
import json
import logging
import threading

log = logging.getLogger("gnb_o1_adaptor_sim")

# Initial configuration of a managed function: the TX-muting leaves, feature enabled and muting off.
DEFAULT_CONFIG = {"txMutingFeatureEnable": "true", "txPathOffPattern": "HORIZONTAL_PLANE", "txMutingActivation": "MUTING_OFF"}
FAULT_MODES = ("TIMEOUT", "RPC_ERROR", "IGNORE_WRITE")


class EventLog:
    """A bounded, numbered log with a blocking read: `since(n, wait)` returns events after sequence n, waiting up to
    `wait` seconds for the first one. Every sink (HTTP long poll, CLI) reads the same log."""

    def __init__(self, size: int = 1000):
        self._events = collections.deque(maxlen=size)
        self._seq = 0
        self._cond = threading.Condition()

    def emit(self, kind: str, **data) -> dict:
        with self._cond:
            self._seq += 1
            event = {"seq": self._seq, "time": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
                     "kind": kind, "data": data}
            self._events.append(event)
            self._cond.notify_all()
        log.info("event %s %s", kind, json.dumps(data, default=str))  # also in `docker compose logs`
        return event

    @property
    def last_seq(self) -> int:
        return self._seq

    def since(self, seq: int = 0, wait: float = 0.0, limit: int = 200) -> list[dict]:
        with self._cond:
            if wait and not any(e["seq"] > seq for e in self._events):
                self._cond.wait(wait)
            return [e for e in self._events if e["seq"] > seq][:limit]


class Store:
    """Configuration per (managed element, function) and the alarms and faults the simulator holds."""

    def __init__(self):
        self.lock = threading.Lock()
        self.config: dict[tuple[str, str | None], dict] = {}
        self.alarms: dict[str, dict] = {}  # RAN NF OAM alarmId -> what was raised
        self.faults: list[dict] = []

    def current(self, ref: str, function_ref: str | None) -> dict:
        with self.lock:
            return {**DEFAULT_CONFIG, **self.config.get((ref, function_ref), {})}

    def merge(self, ref: str, function_ref: str | None, changes: dict) -> dict:
        with self.lock:
            state = {**DEFAULT_CONFIG, **self.config.get((ref, function_ref), {}), **changes}
            self.config[(ref, function_ref)] = state
            return dict(state)

    def take_fault(self) -> str | None:
        """The mode of the next pending fault, consumed once per edit-config."""
        with self.lock:
            if not self.faults:
                return None
            fault = self.faults[0]
            fault["count"] -= 1
            if fault["count"] <= 0:
                self.faults.pop(0)
            return fault["mode"]

    def reset(self) -> None:
        with self.lock:
            self.config.clear()
            self.alarms.clear()
            self.faults.clear()
