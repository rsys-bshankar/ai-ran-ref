"""Thin HTTP client every module uses to call another module THROUGH R1
Termination, rather than service-to-service directly — matching the
topology every LLD assumes (rApp -> R1 Termination -> backend SMOS) and
extending it to SMO-internal cross-module calls (e.g. SO SMOS dispatching
a CONFIG step to RAN NF OAM, NFO calling FOCOM's QueryInventory).

Phase 1: R1 Termination's routing table (Foundational Platform LLD section 4.2)
maps path prefixes to backend services on the same Docker network, so this
client is a small wrapper over httpx rather than a real service mesh client.

Authentication: R1 Termination introspects a Bearer token against SME on
every proxied route (r1-termination/app/main.py's _authorized — "auth:
oauth2 on every backend route except /bootstrap"). SMO-internal callers go
through that same gate, so unless a caller passes its own bearer_token, the
client obtains one the way an rApp does: the token endpoint advertised by
R1's /bootstrap, a CAPIF API-invoker identity onboarded once at SME, and a
client_credentials grant. One identity and one cached token per process;
refreshed shortly before expiry, and once on a 401. Before this, every
internal call was sent without any Authorization header and R1 rejected it
(401), which silently failed e.g. Onboarding's NFO CreateDescriptor call and
rApp Management's package-status check on a real deployment. The
in-process integration mesh (tests_integration/mesh.py) bypasses R1's
gateway mechanics, so it never saw this.

SMO_INVOKER_ID / SMO_INVOKER_SECRET pin a pre-provisioned invoker instead
of onboarding a fresh one at first use.
"""

import logging
import os
import secrets
import threading
import time

import httpx

R1_GATEWAY_URL = os.environ.get("R1_GATEWAY_URL", "http://r1-termination:8000")

log = logging.getLogger(__name__)

_EXPIRY_MARGIN_SECONDS = 30


class _ModuleIdentity:
    """This process's OAuth2 client identity at SME, and its cached token."""

    def __init__(self):
        self.lock = threading.Lock()
        self.invoker_id: str | None = os.environ.get("SMO_INVOKER_ID") or None
        self.invoker_secret: str | None = os.environ.get("SMO_INVOKER_SECRET") or None
        self.token_endpoint: str | None = None
        self.token: str | None = None
        self.expires_at = 0.0

    def _discover(self, base_url: str) -> str:
        if self.token_endpoint is None:
            resp = httpx.get(f"{base_url}/bootstrap", timeout=5.0)
            resp.raise_for_status()
            uris = [(ep.get("tokenEndPoint") or {}).get("uri") for ep in resp.json().get("apiEndpoints", [])]
            self.token_endpoint = next(u for u in uris if u)
        return self.token_endpoint

    def _onboard(self, token_endpoint: str) -> None:
        # SME stores apiInvokerPublicKey but verifies nothing against it (its
        # token endpoint checks only the onboarding secret), so an opaque
        # per-process label is the honest value here, not a real key.
        sme = token_endpoint.rsplit("/oauth2/token", 1)[0]
        label = f"smo-module:{os.environ.get('MODULE', 'unknown')}:{secrets.token_urlsafe(8)}"
        resp = httpx.post(f"{sme}/invoker-registrations", json={"apiInvokerPublicKey": label}, timeout=5.0)
        resp.raise_for_status()
        body = resp.json()
        self.invoker_id, self.invoker_secret = body["apiInvokerId"], body["onboardingSecret"]

    def _grant(self, token_endpoint: str) -> httpx.Response:
        return httpx.post(token_endpoint, json={
            "grant_type": "client_credentials", "client_id": self.invoker_id,
            "client_secret": self.invoker_secret, "scope": "smo-internal",
        }, timeout=5.0)

    def token_for(self, base_url: str, refresh: bool = False) -> str | None:
        with self.lock:
            if not refresh and self.token and time.time() < self.expires_at:
                return self.token
            try:
                token_endpoint = self._discover(base_url)
                if self.invoker_id is None:
                    self._onboard(token_endpoint)
                resp = self._grant(token_endpoint)
                if resp.status_code == 400:   # SME no longer knows this invoker: onboard afresh, once
                    self._onboard(token_endpoint)
                    resp = self._grant(token_endpoint)
                resp.raise_for_status()
            except (httpx.HTTPError, StopIteration, KeyError, ValueError) as exc:
                # No token: the call still goes out and R1 answers 401, which
                # every caller already handles as an ordinary failed call.
                log.warning("could not obtain an SMO access token from SME: %r", exc)
                self.token = None
                return None
            body = resp.json()
            self.token = body["access_token"]
            self.expires_at = time.time() + max(int(body.get("expires_in", 60)) - _EXPIRY_MARGIN_SECONDS, 1)
            return self.token


_identity = _ModuleIdentity()


def _module_token(base_url: str, refresh: bool = False) -> str | None:
    return _identity.token_for(base_url, refresh)


class R1Client:
    def __init__(self, base_url: str = R1_GATEWAY_URL, bearer_token: str | None = None):
        self.base_url = base_url
        self._bearer_token = bearer_token

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self, refresh: bool = False) -> dict:
        token = self._bearer_token or _module_token(self.base_url, refresh)
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _send(self, send, path: str, **kwargs) -> httpx.Response:
        resp = send(self._url(path), headers=self._headers(), **kwargs)
        if resp.status_code == 401 and self._bearer_token is None:
            # expired or revoked at SME since it was cached: one fresh token, one retry
            resp = send(self._url(path), headers=self._headers(refresh=True), **kwargs)
        return resp

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._send(httpx.get, path, **kwargs)

    def post(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response:
        return self._send(httpx.post, path, json=json, **kwargs)

    def put(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response:
        return self._send(httpx.put, path, json=json, **kwargs)

    def delete(self, path: str, **kwargs) -> httpx.Response:
        return self._send(httpx.delete, path, **kwargs)
