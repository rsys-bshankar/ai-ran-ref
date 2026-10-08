"""The check registry, the context a check runs in, and the report."""

import dataclasses
import datetime
import json
import time
import uuid
from collections.abc import Callable

import httpx

PASS, FAIL, SKIP = "pass", "fail", "skip"


class Fail(Exception):
    """A check's way of saying the adaptor broke the contract; the message says how."""


class Skip(Exception):
    """A check that does not apply (a protocol the adaptor does not declare, a service it does not offer)."""


@dataclasses.dataclass(frozen=True)
class Check:
    id: str
    group: str                       # DISC, NETCONF, RESTCONF
    title: str
    fn: Callable


@dataclasses.dataclass
class Result:
    id: str
    group: str
    title: str
    status: str
    detail: str = ""
    seconds: float = 0.0


REGISTRY: list[Check] = []


def check(check_id: str, group: str, title: str):
    def register(fn):
        REGISTRY.append(Check(check_id, group, title, fn))
        return fn
    return register


class Context:
    """What a check gets: the HTTP client (its base_url is the adaptor), and managed-object references that are new to this run, so a second run
    against the same adaptor does not meet the first one's leftovers."""

    def __init__(self, client: httpx.Client, protocols: set[str], declared: dict | None = None, *, netconf_path: str = "/edit-config",
                 restconf_root: str = "/restconf", capabilities_path: str = "/capabilities"):
        self.client = client
        self.protocols = protocols
        self.declared = declared or {}
        self.netconf_path = netconf_path
        self.restconf_root = restconf_root.rstrip("/")
        self.capabilities_path = capabilities_path
        self.run_id = uuid.uuid4().hex[:8]
        self._n = 0

    def new_ref(self, prefix: str = "conf") -> str:
        self._n += 1
        return f"{prefix}-{self.run_id}-{self._n}"


def run(ctx: Context, only: set[str] | None = None) -> list[Result]:
    results: list[Result] = []
    for item in REGISTRY:
        if only and item.id not in only and item.group not in only:
            continue
        started = time.perf_counter()
        try:
            if item.group in ("NETCONF", "RESTCONF") and item.group.lower() not in ctx.protocols:
                raise Skip(f"{item.group.lower()} is not part of this run")
            item.fn(ctx)
            status, detail = PASS, ""
        except Fail as exc:
            status, detail = FAIL, str(exc)
        except Skip as exc:
            status, detail = SKIP, str(exc)
        except httpx.HTTPError as exc:
            status, detail = FAIL, f"no usable answer: {type(exc).__name__}: {exc}"
        results.append(Result(item.id, item.group, item.title, status, detail, round(time.perf_counter() - started, 3)))
    return results


def summary(results: list[Result]) -> dict:
    return {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP)}


def to_json(adaptor: str, protocols: set[str], results: list[Result]) -> str:
    return json.dumps({"adaptor": adaptor, "protocols": sorted(protocols), "finished": datetime.datetime.now(datetime.UTC).isoformat(),
                       "summary": summary(results), "results": [dataclasses.asdict(r) for r in results]}, indent=2)


def to_markdown(adaptor: str, protocols: set[str], results: list[Result]) -> str:
    counts = summary(results)
    lines = [f"# O1 adaptor conformance: {adaptor}", "",
             f"Protocols run: {', '.join(sorted(protocols)) or 'none'}. **{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped.**", "",
             "| Check | Group | What | Result | |", "|---|---|---|---|---|"]
    mark = {PASS: "pass", FAIL: "**FAIL**", SKIP: "skipped"}
    lines += [f"| {r.id} | {r.group} | {r.title} | {mark[r.status]} | {r.detail.replace('|', '/')} |" for r in results]
    return "\n".join(lines) + "\n"
