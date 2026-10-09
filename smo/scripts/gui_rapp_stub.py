#!/usr/bin/env python3
"""A stand-in rApp for the browser check of the declared rApp pages (GUI-8.7, scripts/gui_rapp_pages_e2e.py).

    gui_rapp_stub.py serve --csar PATH [--port 8898] [--public http://r1-termination:8898]
    gui_rapp_stub.py setup --public http://r1-termination:8898 [--onboarding http://onboarding:8000] [--rapp-mgmt http://rapp-mgmt:8000]

`serve` is a plain HTTP server (standard library only) that is two things at once:

  - the package host: `/writable.csar` is the Energy Saving sample package (PATH) as it is, whose manifest carries an `operatorUi`; `/readonly.csar` is the same package renamed
    `EnergySaving_rApp_ReadOnly` (in the manifest and in the ASD, which is where Onboarding reads the name) whose `operatorUi` is replaced by a small page that is `readOnly: true`;
  - the rApp's operator API, the routes the Energy Saving declaration names, answering with canned cells (two, one of them overridden by "alice") and keeping the override in memory, so a
    click on a row action changes what the next read shows. `GET /_calls` lists the calls it received (the check reads it to see that a click reached the rApp and carried the user).

`setup` onboards both packages from `--public`, waits for them to be AVAILABLE and creates one instance of each with `operatorApiBase` = `--public`, then prints
`{"writable": <instanceId>, "readOnly": <instanceId>}`. It does what DEMO_RUNBOOK.md and the sample `demo.py` do with their own packages; no GUI build is involved, which is the point.

Run it inside the compose network, where the platform can reach it: the check copies this file and the CSAR into the `r1-termination` container (the runbook's /srv/scratch) and runs it there.
"""

import argparse
import io
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

READ_ONLY_NAME = "EnergySaving_rApp_ReadOnly"
READ_ONLY_PAGE = """\
operatorUi:
  version: 1
  readOnly: true
  panels:
    - id: instance
      title: Instance
      kind: keyValues
      source:
        path: "/instances/{instanceId}"
      items:
        - {label: Managed element, path: managedElementRef}
        - {label: Autonomy mode, path: autonomyMode, format: badge}
"""
INSTANCE = {"managedElementRef": "gnb-du-e2e-01", "autonomyMode": "AUTONOMOUS", "actuator": "ADMINISTRATIVE_STATE", "modelId": "00000000-0000-0000-0000-0000000000e2", "modelVersion": 3}


def read_only_package(csar: bytes) -> bytes:
    """Returns the sample CSAR with the name `EnergySaving_rApp_ReadOnly` and a small `readOnly: true` page in place of its `operatorUi` block.

        Rewrites only `manifest.yaml` (cut at the `operatorUi:` line, which must be the last block; the comment block above it is dropped) and the
        `application_name` of `Definitions/asd.yaml`; every other member is copied as it is. Raises `ValueError` if the manifest has no `operatorUi:` line.
    """
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(csar)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "manifest.yaml":
                text = data.decode("utf-8")
                head = text[:text.index("\noperatorUi:") + 1]
                head = re.sub(r"^# The page the operator console draws.*?(?=^operatorUi:|\Z)", "", head, flags=re.S | re.M)
                text = re.sub(r"^name: .*$", f"name: {READ_ONLY_NAME}", head, count=1, flags=re.M) + READ_ONLY_PAGE
                data = text.encode("utf-8")
            elif info.filename == "Definitions/asd.yaml":          # the name Onboarding records is the ASD's application_name
                data = re.sub(r"^(\s*application_name:).*$", rf"\1 {READ_ONLY_NAME}", data.decode("utf-8"), count=1, flags=re.M).encode("utf-8")
            target.writestr(info, data)
    return out.getvalue()


class Rapp:
    """The state of the stub rApp: which cells are overridden and by whom, and every call received. `lock` guards both, because the HTTP server answers in threads."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.overrides: dict[str, str] = {"102": "alice"}
        self.calls: list[dict] = []

    def cells(self) -> list[dict]:
        """The two canned cells (101 and 102) for the dashboard route, each with a 12-point PRB trend and its latest decision; `overrideBy` shows the current override."""
        out = []
        for cell in ("101", "102"):
            trend = [{"t": f"2026-01-01T00:{m:02d}:00Z", "v": 30 + (m * 7 + int(cell)) % 40} for m in range(12)]
            out.append({"cellId": cell, "state": "ACTIVE", "overrideBy": self.overrides.get(cell), "o1Value": "UNLOCKED", "prbTrend": trend,
                        "latestDecision": {"decision": "KEEP_ON", "reason": f"load stays high in cell {cell}", "outcome": "VERIFIED", "executionId": f"exec-{cell}",
                                           "prediction": {"model": {"futurePrb": 61.0}}}})
        return out


RAPP = Rapp()


class Handler(BaseHTTPRequestHandler):
    """The stub's request handler: serves the two packages, `GET /_calls`, and the rApp's operator routes under `/instances/<id>`."""
    server_version = "gui-rapp-stub"
    csar: bytes = b""

    def log_message(self, *args) -> None:        # quiet
        pass

    def _send(self, status: int, body: bytes, kind: str = "application/json") -> None:
        """Writes a complete response (status, `Content-Type`, `Content-Length`, body)."""
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value, status: int = 200) -> None:
        self._send(status, json.dumps(value).encode())

    def _body(self):
        """The request body: parsed JSON, the decoded text when it is not JSON, or None when there is none."""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return raw.decode("utf-8", "replace")

    def _route(self, method: str) -> None:
        """Answers one request. The package and `/_calls` routes are not logged; every other request is appended to `RAPP.calls` (with the invoker id and the user
            header the platform forwarded) before it is answered, so the check sees the calls that were refused with 404 too.

            The operator routes are `GET /instances/<id>`, `/dashboard`, `/decisions`, `POST .../evaluate` and `.../reconcile`, and `POST`/`DELETE .../cells/<cell>/override`;
            the override is kept in memory so the next read shows it. Anything else is `404 {"title": "not found"}`.
        """
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if method == "GET" and path == "/writable.csar":
            return self._send(200, self.csar, "application/zip")
        if method == "GET" and path == "/readonly.csar":
            return self._send(200, read_only_package(self.csar), "application/zip")
        if method == "GET" and path == "/_calls":
            with RAPP.lock:
                return self._json(RAPP.calls)
        body = self._body() if method != "GET" else None
        with RAPP.lock:
            RAPP.calls.append({"method": method, "path": path, "query": {k: v[0] for k, v in query.items()}, "body": body,
                               "invoker": self.headers.get("X-R1-Invoker-Id"), "via": self.headers.get("X-Smo-Operator-User") or self.headers.get("X-Forwarded-User")})
            match = re.fullmatch(r"/instances/([^/]+)(/.*)?", path)
            if not match:
                return self._json({"title": "not found"}, 404)
            rest = match.group(2) or ""
            if method == "GET" and rest == "":
                return self._json({"instanceId": match.group(1), **INSTANCE})
            if method == "GET" and rest == "/dashboard":
                return self._json({"cells": RAPP.cells()})
            if method == "GET" and rest == "/decisions":
                cell = (query.get("cell_id") or ["101"])[0]
                return self._json({"items": [{"observedAt": f"2026-01-01T00:{m:02d}:00Z", "prb": 40 + m, "decision": "KEEP_ON", "reason": f"cell {cell}", "outcome": "VERIFIED",
                                              "executionId": f"exec-{cell}-{m}"} for m in range(3)]})
            if method == "POST" and rest in ("/evaluate", "/reconcile"):
                return self._json({"ok": True})
            override = re.fullmatch(r"/cells/([^/]+)/override", rest)
            if override and method == "POST":
                RAPP.overrides[override.group(1)] = (body or {}).get("operator", "?") if isinstance(body, dict) else "?"
                return self._json({"ok": True})
            if override and method == "DELETE":
                RAPP.overrides.pop(override.group(1), None)
                return self._json({"ok": True})
        return self._json({"title": "not found"}, 404)

    def do_GET(self) -> None:    # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:   # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:    # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")


def call(method: str, url: str, body: dict | None = None) -> dict:
    """One JSON request to a platform service; returns the parsed answer ({} for an empty one). Ends the script with the status and the first 300 bytes on an HTTP error."""
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})  # noqa: S310 (a URL on the compose network, given by the check)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:      # noqa: S310 (a URL on the compose network, given by the check)
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {url} -> {error.code} {error.read()[:300]!r}") from None


def onboard(onboarding: str, location: str) -> str:
    """Asks Onboarding to onboard the package at `location` and polls its status for up to about a minute; returns the package id once it is AVAILABLE or PRIMED.

        Ends the script with a message when the state is FAILED or the polling runs out.
    """
    package = call("POST", f"{onboarding}/packages", {"location": location})["packageId"]
    for _ in range(60):
        status = call("GET", f"{onboarding}/packages/{package}/onboarding-status")
        if status["state"] in ("AVAILABLE", "PRIMED"):
            return package
        if status["state"] == "FAILED":
            raise SystemExit(f"onboarding of {location} FAILED: {status}")
        time.sleep(1)
    raise SystemExit(f"onboarding of {location} did not finish")


def setup(public: str, onboarding: str, rapp_mgmt: str) -> dict:
    """Onboards the writable and the read-only package from the stub's `public` address and creates one instance of each through rApp Management.

        Returns `{"writable": <instanceId>, "readOnly": <instanceId>}`. Each instance's `operatorApiBase` is `public`, which is how the platform reaches the stub.
        Creates real packages and instances on the stack; it does not clean up.
    """
    ids = {}
    for key, name in (("writable", "writable.csar"), ("readOnly", "readonly.csar")):
        package = onboard(onboarding, f"{public}/{name}")
        instance = call("POST", f"{rapp_mgmt}/instances", {"operatorApiBase": public, "packageId": package, "autonomyMode": "AUTONOMOUS",
                                                          "config": {"managedElementRef": INSTANCE["managedElementRef"], "cells": [101, 102], "actuator": "ADMINISTRATIVE_STATE"},
                                                          "regionScope": {"objectInstance": INSTANCE["managedElementRef"], "cells": [101, 102]}})
        ids[key] = instance["instanceId"]
    return ids


def main(argv: list[str] | None = None) -> int:
    """Command-line entry: `serve` runs the stub until it is killed; `setup` prints the instance ids as JSON. Returns 0."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--csar", required=True)
    serve.add_argument("--port", type=int, default=8898)
    serve.add_argument("--host", default="0.0.0.0")      # noqa: S104 (inside the compose network, reached by the platform)
    setup_parser = sub.add_parser("setup")
    setup_parser.add_argument("--public", required=True)
    setup_parser.add_argument("--onboarding", default="http://onboarding:8000")
    setup_parser.add_argument("--rapp-mgmt", default="http://rapp-mgmt:8000")
    args = ap.parse_args(argv)
    if args.command == "serve":
        Handler.csar = open(args.csar, "rb").read()
        print(f"serving on {args.host}:{args.port}", flush=True)
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
        return 0
    print(json.dumps(setup(args.public.rstrip("/"), args.onboarding.rstrip("/"), args.rapp_mgmt.rstrip("/"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
