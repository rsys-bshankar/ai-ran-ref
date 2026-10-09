"""PR-HA-4.1: a destination that records what it is sent, slowly, so a batch takes long enough to be interrupted.

Runs in a throwaway pod in the cluster (the CI job `helm`): every POST is appended to /tmp/received.txt (its path) and answered 200 after
SINK_DELAY seconds; `GET /count` answers "<received> <distinct>" as text.
"""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = float(os.environ.get("SINK_DELAY", "0.3"))
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    """The sink's request handler: a POST is recorded after the delay, any GET answers the running counts. Request logging is switched off."""
    def log_message(self, *args):
        pass

    def do_POST(self):
        """Reads and discards the body, waits `DELAY` seconds, appends the request path to `/tmp/received.txt` and answers 200 with no body.

            The delay is what keeps a batch of notifications in flight long enough for the CI job to interrupt the sender (PR-HA-4.1).
        """
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        time.sleep(DELAY)
        with LOCK, open("/tmp/received.txt", "a") as out:  # noqa: S108 (a throwaway pod's own file)
            out.write(self.path + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        """Answers (whatever the path) "<received> <distinct>" as text: how many paths were recorded and how many of them differ.

            A difference between the two numbers is a notification delivered more than once.
        """
        with LOCK:
            lines = open("/tmp/received.txt").read().split() if os.path.exists("/tmp/received.txt") else []  # noqa: S108
        body = f"{len(lines)} {len(set(lines))}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()  # noqa: S104
