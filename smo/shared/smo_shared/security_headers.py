"""Response headers every module sends (PR-V-7, found by the ZAP API scan of the gateway).

  X-Content-Type-Options: nosniff             a browser does not guess a content type: a JSON answer is never run as a script or shown as HTML.
  Cross-Origin-Resource-Policy: same-origin   another origin cannot load an answer of ours as a no-cors resource (an image, a script, a worker).

The R1 APIs answer JSON to API clients; a browser reaches them through the GUI's own origin (its proxy), so neither header costs a legitimate caller anything.
A route that must differ sets the header itself: `setdefault` leaves it alone. `apply_correlation_id` installs this for every module, like the invoker context.
"""

from fastapi import FastAPI, Request

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def apply_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        return response
