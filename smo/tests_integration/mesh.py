"""In-process service mesh — makes cross-module httpx calls (R1Client,
A1TerminationClient) land on the right module's FastAPI TestClient
instead of going out over the network.

Deliberate scope choice: this bypasses R1 Termination's own proxy
mechanics (already covered by r1-termination/tests) and dispatches
directly, path-prefix-to-module, exactly as R1 Termination's ROUTES
table does. That keeps these tests focused on cross-module BUSINESS
LOGIC — does calling NFO actually make FOCOM's inventory get queried,
does A1 Related's create_policy actually reach the mock Near-RT RIC —
rather than re-proving the gateway's HTTP forwarding a second time.
"""

from urllib.parse import urlparse

# mirrors r1-termination/app/main.py's ROUTES table
R1_PREFIX_TO_SERVICE = {
    "/sme": "sme", "/dme": "dme", "/dme-push": "dme", "/dme-pull": "dme",
    "/onboarding": "onboarding", "/rapp-mgmt": "rapp-mgmt", "/ran-nf-oam": "ran-nf-oam",
    "/a1-related": "a1-related", "/nfo": "nfo", "/focom": "focom",
    "/ai-ml-workflow": "ai-ml-workflow", "/ran-analytics": "ran-analytics",
    "/policy-mgmt": "policy-mgmt", "/so-smos": "so-smos", "/sa-smos": "sa-smos",
}


class ServiceMesh:
    def __init__(self, clients: dict[str, "TestClient"]):
        """clients: {service-dir-name: TestClient}, e.g. {"sme": <TestClient>, "mock-near-rt-ric": <TestClient>}."""
        self.clients = clients

    def resolve(self, url: str) -> tuple["TestClient", str]:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path

        if host == "r1-termination":
            segments = path.lstrip("/").split("/", 1)
            prefix = "/" + segments[0]
            service = R1_PREFIX_TO_SERVICE.get(prefix)
            if service is None:
                raise LookupError(f"no route for {prefix} in the mesh's R1 routing table")
            rest = "/" + segments[1] if len(segments) > 1 else "/"
            return self.clients[service], rest

        # direct calls that bypass R1 Termination entirely — e.g. A1 Related's
        # a1_termination_client talking to the isolated mock Near-RT RIC,
        # matching SMO Design v1.3 section 3.9's actual topology (never
        # routed through R1 Termination at all).
        if host in self.clients:
            return self.clients[host], path

        raise LookupError(f"mesh has no route for host {host!r} ({url})")

    def dispatch(self, verb: str, url: str, *, json=None, params=None, headers=None, content=None, timeout=None, **kwargs):
        client, rest_path = self.resolve(url)
        method = getattr(client, verb)
        # httpx's own GET/DELETE signatures have no `json` parameter at all
        # (only POST/PUT/PATCH do) — passing it unconditionally raises
        # TypeError. Caught while running this harness for the first time:
        # every GET routed through the mesh (NFO's FOCOM inventory query,
        # A1 Related's live status refresh) was hitting this before the fix.
        if verb in ("get", "delete"):
            return method(rest_path, params=params, headers=headers)
        # RAN NF OAM's netconf_client.py posts a raw XML body via `content=`,
        # not `json=` — every other caller until now was JSON-only, so this
        # branch never existed and `content` was silently dropped, always
        # forwarding an EMPTY body regardless of what the real caller sent.
        # Caught adding mock-o1-adaptor's own integration test (OPEN_ITEMS.md
        # section 2): the request "succeeded" against an empty body and
        # looked like a genuine REJECTED outcome, not a harness bug.
        if content is not None:
            return method(rest_path, content=content, params=params, headers=headers)
        return method(rest_path, json=json, params=params, headers=headers)


def install(monkeypatch, mesh: ServiceMesh) -> None:
    """Monkeypatches the plain httpx.get/post/put/delete module functions —
    both smo_shared.r1_client.R1Client and a1-related's
    A1TerminationClient call these directly (`import httpx; httpx.post(...)`),
    so one patch at the httpx module level intercepts everything, regardless
    of which module's code makes the call.
    """
    import httpx

    from smo_shared import r1_client

    for verb in ("get", "post", "put", "delete"):
        monkeypatch.setattr(httpx, verb, (lambda v: lambda url, **kw: mesh.dispatch(v, url, **kw))(verb))
    # The mesh dispatches straight to each module, bypassing R1 Termination's
    # token gate along with the rest of its gateway mechanics (see the module
    # docstring) — so R1Client's SME token acquisition is skipped too. The
    # token flow itself is covered by shared/tests/test_r1_client.py.
    monkeypatch.setattr(r1_client, "_module_token", lambda base_url, refresh=False: None)
