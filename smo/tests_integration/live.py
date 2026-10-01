"""Live mode for the DEMO_RUNBOOK replay (OI-2-compose-e2e).

`SMO_E2E_LIVE=1` points `test_demo_runbook.py` at a running docker-compose
stack instead of the in-process mesh: the test process runs in a container
attached to the compose network, so every module answers on
`http://<service>:8000` exactly as it does for the runbook's own
`docker compose exec` commands. The same test file therefore proves the
runbook two ways, and the two cannot drift apart.

Two things the in-process run got for free need a real stand-in:

- **The consumer the runbook calls back** (`http://demo-consumer:9000/...`).
  `Receiver` listens on :9000 (the test container carries the network alias
  `demo-consumer`) and records every POST, so the notification assertions
  check real deliveries instead of an intercepted `httpx.post`.
- **The package server** (the runbook's §1 `http.server`). The same
  `Receiver` serves `samples/*.csar` under `/csar/`.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx

LIVE = bool(os.environ.get("SMO_E2E_LIVE"))
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
CALLBACK_HOST = "demo-consumer"
CALLBACK_PORT = 9000
CALLBACK_BASE = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}"


class Receiver:
    """Records POST bodies by path; serves the sample packages."""

    def __init__(self):
        self._lock = threading.Lock()
        self._received: list[tuple[str, object]] = []
        receiver = self
        csars = {f.name: f.read_bytes() for f in SAMPLES.glob("*.csar")}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    payload = json.loads(body) if body else None
                except ValueError:
                    payload = body.decode(errors="replace")
                with receiver._lock:
                    receiver._received.append((f"{CALLBACK_BASE}{self.path}", payload))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                # a fixed table of the built packages: the request only selects a key
                data = csars.get(self.path.removeprefix("/csar/")) if self.path.startswith("/csar/") else None
                if data is not None:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self._server = ThreadingHTTPServer(("0.0.0.0", CALLBACK_PORT), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def since(self, mark: int, url_prefix: str, with_location: bool):
        with self._lock:
            hits = [(loc, body) for loc, body in self._received[mark:] if loc.startswith(url_prefix)]
        return hits if with_location else [body for _, body in hits]

    def mark(self) -> int:
        with self._lock:
            return len(self._received)


class LiveCapture:
    """A list-like view of the deliveries to one callback URL (prefix) since
    `capture()` was called — re-read on every access, because the module
    delivers during the request, and the test asserts right after it."""

    def __init__(self, receiver: Receiver, url_prefix: str, with_location: bool):
        self._receiver, self._prefix, self._with_location = receiver, url_prefix, with_location
        self._mark = receiver.mark()

    def _items(self):
        return self._receiver.since(self._mark, self._prefix, self._with_location)

    def __len__(self):
        return len(self._items())

    def __getitem__(self, i):
        return self._items()[i]

    def __iter__(self):
        return iter(self._items())

    def __eq__(self, other):
        return self._items() == other


def live_mesh() -> dict:
    """`mesh[<service>]` -> an httpx client on that service's own port."""
    class Mesh(dict):
        def __missing__(self, name):
            self[name] = client = httpx.Client(base_url=f"http://{name}:8000", timeout=120.0)
            return client

    return Mesh()


def stub_apps() -> dict:
    """Stand-ins for `loaded_apps`: tests `monkeypatch.setattr` on an app's
    `.httpx`; in live mode there is no in-process app, so that is a no-op."""
    class Apps(dict):
        def __missing__(self, name):
            self[name] = SimpleNamespace(httpx=SimpleNamespace(get=None, post=None, put=None, delete=None))
            return self[name]

    return Apps()
