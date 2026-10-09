"""The check registry, the two contexts a check runs in (a package file, a running stack) and the report."""

import dataclasses
import datetime
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"
PACKAGE, RUNTIME = "package", "runtime"                   # the two kinds of check; `python -m conformance.rapp package|runtime` runs one kind


class Fail(Exception):
    """A check's way of saying the package or the platform broke the contract; the message says how."""


class Warn(Exception):
    """Advice, not a refusal: Onboarding accepts this, but the package will be harder to use or to trust."""


class Skip(Exception):
    """A check that does not apply (no trust store given, an earlier step failed)."""


@dataclasses.dataclass(frozen=True)
class Check:
    id: str
    group: str
    title: str
    kind: str
    fn: Callable


@dataclasses.dataclass
class Result:
    id: str
    group: str
    title: str
    status: str
    detail: str = ""
    seconds: float = 0.0
    subject: str = ""            # the package a check ran on (the package kind); empty for the runtime kind


REGISTRY: list[Check] = []


def check(check_id: str, group: str, title: str, kind: str):
    def register(fn):
        REGISTRY.append(Check(check_id, group, title, kind, fn))
        return fn
    return register


class PackageContext:
    """One package file under test. `trust` is an optional `csar_signing.TrustStore`; `require_signed` makes an unsigned package a failure."""

    kind = PACKAGE

    def __init__(self, path: Path | str, data: bytes, trust=None, *, require_signed: bool = False):
        self.path = str(path)
        self.name = Path(path).name
        self.data = data
        self.trust = trust
        self.require_signed = require_signed
        self.cache: dict = {}                      # what an early check worked out (the parsed zip, the verdict), for the later ones

    @property
    def subject(self) -> str:
        return self.name


class RuntimeContext:
    """A running stack. `services` maps a module name (`onboarding`, `rapp-mgmt`) to an httpx-like client whose paths are that module's own (`/packages`): in
    production the kit builds them over R1 (`<r1>/<service>/...`) or straight at the modules; the tests give the in-process mesh's clients. `package_url` is
    where Onboarding can fetch the CSAR from, which need not be where the kit runs."""

    kind = RUNTIME

    def __init__(self, services: dict, package_url: str, *, autonomy_mode: str = "SHADOW", keep: bool = False):
        self.services = services
        self.package_url = package_url
        self.autonomy_mode = autonomy_mode
        self.keep = keep                                  # leave the package onboarded and the instance deleted (a failed run leaves what it made either way)
        self.run_id = uuid.uuid4().hex[:8]
        self.state: dict = {}                             # package_id, instance_id, ... : what the steps built on each other
        self.cleanups: list[Callable[[], None]] = []
        self.subject = ""

    def call(self, service: str, verb: str, path: str, **kwargs) -> httpx.Response:
        return getattr(self.services[service], verb)(path, **kwargs)

    def need(self, key: str, because: str):
        """The result of an earlier step, or Skip: a step that cannot run is reported as skipped, with the step it needed, not as a second failure."""
        if key not in self.state:
            raise Skip(f"needs {because}, which did not happen")
        return self.state[key]


def run(ctx, only: set[str] | None = None) -> list[Result]:
    results: list[Result] = []
    for item in REGISTRY:
        if item.kind != ctx.kind or (only and item.id not in only and item.group not in only):
            continue
        started = time.perf_counter()
        try:
            item.fn(ctx)
            status, detail = PASS, ""
        except Fail as exc:
            status, detail = FAIL, str(exc)
        except Warn as exc:
            status, detail = WARN, str(exc)
        except Skip as exc:
            status, detail = SKIP, str(exc)
        except httpx.HTTPError as exc:
            status, detail = FAIL, f"no usable answer: {type(exc).__name__}: {exc}"
        results.append(Result(item.id, item.group, item.title, status, detail, round(time.perf_counter() - started, 3), ctx.subject))
    for cleanup in reversed(getattr(ctx, "cleanups", [])):
        try:
            cleanup()
        except Exception:        # noqa: BLE001  (a cleanup that fails must not hide the verdict; the report lists what it left)
            results.append(Result("CLEANUP", "RUNTIME", "what the run created is removed", WARN, "a clean-up step failed: the run's instance or package may still exist", 0.0, ctx.subject))
    return results


def summary(results: list[Result]) -> dict:
    return {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, WARN, SKIP)}


def to_json(title: str, results: list[Result], extra: dict | None = None) -> str:
    return json.dumps({"kit": "conformance.rapp", "subject": title, **(extra or {}), "finished": datetime.datetime.now(datetime.UTC).isoformat(),
                       "summary": summary(results), "results": [dataclasses.asdict(r) for r in results]}, indent=2)


def to_markdown(title: str, results: list[Result], intro: str = "") -> str:
    counts = summary(results)
    lines = [f"# rApp conformance: {title}", "", (intro + " " if intro else "") + f"**{counts[PASS]} passed, {counts[FAIL]} failed, {counts[WARN]} warnings, {counts[SKIP]} skipped.**", ""]
    packages = len({r.subject for r in results if r.subject}) > 1
    lines += ["| Check | Group | " + ("Package | " if packages else "") + "What | Result | |", "|---|---|" + ("---|" if packages else "") + "---|---|---|"]
    mark = {PASS: "pass", FAIL: "**FAIL**", WARN: "warning", SKIP: "skipped"}
    for r in results:
        lines.append(f"| {r.id} | {r.group} | " + (f"{r.subject} | " if packages else "") + f"{r.title} | {mark[r.status]} | {r.detail.replace('|', '/').replace(chr(10), ' ')} |")
    return "\n".join(lines) + "\n"
