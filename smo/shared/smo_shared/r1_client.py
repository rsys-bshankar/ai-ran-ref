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

PR-SEC-14, who the caller is: an SMO module presents the enrollment secret
(`SMO_ENROLLMENT_SECRET[_FILE]`, a compose secret) when it registers and asks for
the `smo-internal` scope; SME records the invoker as `internal`. A process that
is an rApp (`SMO_IDENTITY_KIND=rapp`, the sample rApps' compose entries) presents
none, is recorded as `rapp`, asks for `smo-rapp`, and keeps its identity under its
own key in the module identity store. R1 Termination applies its role policy on
that record (`roles.py`).
"""

import logging
import os
import secrets
import threading
import time

import httpx

from .correlation import HEADER_NAME as CORRELATION_ID_HEADER
from .correlation import get_correlation_id
from .invoker import ON_BEHALF_OF_HEADER, get_originator
from . import metrics, tracing
from .roles import ENROLLMENT_HEADER, RAPP_SCOPE
from .secretfile import read_secret
from .timeouts import call_timeout

R1_GATEWAY_URL = os.environ.get("R1_GATEWAY_URL", "http://r1-termination:8000")

log = logging.getLogger(__name__)

_EXPIRY_MARGIN_SECONDS = 30


def identity_kind() -> str:
    """`rapp` for a process that is an rApp, else `module` (an SMO service)."""
    return "rapp" if os.environ.get("SMO_IDENTITY_KIND", "module").strip().lower() == "rapp" else "module"


class _ModuleIdentity:
    """This process's OAuth2 client identity at SME, and its cached token."""

    def __init__(self, store=None):
        self.lock = threading.Lock()
        self.invoker_id: str | None = os.environ.get("SMO_INVOKER_ID") or None
        self.invoker_secret: str | None = os.environ.get("SMO_INVOKER_SECRET") or None
        # Where this module's invoker identity is shared with its other replicas (module_identity.py).
        # None = decide on first use: the database store when MODULE is set, else a per-process identity.
        self._store = store
        self._store_resolved = store is not None
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

    def _identity_store(self):
        if not self._store_resolved:
            self._store_resolved = True
            if os.environ.get("MODULE") and os.environ.get("SMO_MODULE_IDENTITY_STORE", "db") != "off":
                from .module_identity import DbIdentityStore
                self._store = DbIdentityStore()
        return self._store

    def _register_at_sme(self, token_endpoint: str) -> tuple[str, str]:
        # An opaque label, not a PEM key: this client authenticates with its onboarding
        # secret, so SME has no key to verify assertions with (an RFC 7523 client
        # assertion needs a PEM key, SA-SME-1-public-key).
        sme = token_endpoint.rsplit("/oauth2/token", 1)[0]
        kind = identity_kind()
        label = f"smo-{'rapp' if kind == 'rapp' else 'module'}:{os.environ.get('MODULE', 'unknown')}:{secrets.token_urlsafe(8)}"
        headers = {}
        enrollment = read_secret("SMO_ENROLLMENT_SECRET") if kind == "module" else None
        if enrollment:
            headers[ENROLLMENT_HEADER] = enrollment
        resp = httpx.post(f"{sme}/invoker-registrations", json={"apiInvokerPublicKey": label}, headers=headers, timeout=5.0)
        resp.raise_for_status()
        body = resp.json()
        return body["apiInvokerId"], body["onboardingSecret"]

    def _onboard(self, token_endpoint: str, stale_invoker_id: str | None = None) -> None:
        """Obtain this module's invoker identity. With a shared store (PR-ST-4) all replicas of the
        module use one invoker: the stored identity is adopted if there is one (other than
        `stale_invoker_id`, which SME refused); otherwise this process registers one and stores it,
        and a replica that loses the race to store discards its own registration and adopts the winner's."""
        module = os.environ.get("MODULE", "unknown")
        if identity_kind() == "rapp":
            module = f"rapp:{module}"            # an rApp's identity is not the module's: they are registered differently
        store = self._identity_store()
        if store is not None:
            try:
                stored = store.load(module)
                if stored is not None and stored[0] != stale_invoker_id:
                    self.invoker_id, self.invoker_secret = stored
                    return
            except Exception as exc:  # the store is an optimisation: without it, register per process as before
                log.warning("module identity store unavailable, registering this process on its own: %r", exc)
                store = None
        invoker_id, secret = self._register_at_sme(token_endpoint)
        if store is not None:
            try:
                stored_ok = (store.insert(module, invoker_id, secret) if stale_invoker_id is None
                             else store.replace(module, stale_invoker_id, invoker_id, secret))
                if not stored_ok:  # another replica stored its identity first: use that one, drop ours
                    self._offboard(token_endpoint, invoker_id)
                    winner = store.load(module)
                    if winner is not None:
                        self.invoker_id, self.invoker_secret = winner
                        return
            except Exception as exc:
                log.warning("could not store the module identity, using this process's own: %r", exc)
        self.invoker_id, self.invoker_secret = invoker_id, secret

    @staticmethod
    def _offboard(token_endpoint: str, invoker_id: str) -> None:
        sme = token_endpoint.rsplit("/oauth2/token", 1)[0]
        try:
            httpx.delete(f"{sme}/invoker-registrations/{invoker_id}", timeout=5.0)
        except httpx.HTTPError as exc:  # an orphan registration is only clutter; SME's stale-invoker purge removes it
            log.warning("could not offboard the duplicate invoker %s: %r", invoker_id, exc)

    def _grant(self, token_endpoint: str) -> httpx.Response:
        return httpx.post(token_endpoint, json={
            "grant_type": "client_credentials", "client_id": self.invoker_id,
            "client_secret": self.invoker_secret, "scope": RAPP_SCOPE if identity_kind() == "rapp" else "smo-internal",
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
                    self._onboard(token_endpoint, stale_invoker_id=self.invoker_id)
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
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # Wave 3 cross-cutting standardization's Correlation-ID slice
        # (smo_shared/correlation.py): propagates the calling handler's
        # own correlation ID to this downstream call, so one inbound
        # request's whole cross-service fan-out shares one ID. None
        # outside any request context (e.g. a standalone script) — no
        # header added rather than fabricating one.
        correlation_id = get_correlation_id()
        if correlation_id:
            headers[CORRELATION_ID_HEADER] = correlation_id
        # Who this call is for (smo_shared/invoker.py): when the request being handled came from an rApp, the safeguards at the next module must
        # still see that rApp, not this module. Nothing is added when the module acts on its own account.
        originator = get_originator()
        if originator:
            headers[ON_BEHALF_OF_HEADER] = originator
        return headers

    def _call(self, send, method: str, path: str, headers: dict, **kwargs) -> httpx.Response:
        """One attempt, counted (PR-OBS-2.6): target module, method and outcome; a transport failure is counted and re-raised."""
        target, started = metrics.r1_target(path), time.perf_counter()
        # PR-OBS-3: one CLIENT span per attempt; the traceparent sent names that span (or, with spans off, the caller's own trace unchanged)
        with tracing.span(f"{method.upper()} {target}", "client", {"http.request.method": method.upper(), "smo.target": target,
                                                                  "smo.correlation_id": get_correlation_id() or ""}) as client_span:
            headers = {**headers, **tracing.inject_headers()}
            try:
                resp = send(self._url(path), headers=headers, **kwargs)
            except httpx.TimeoutException:
                metrics.record_outbound("r1", target, method, "timeout", time.perf_counter() - started)
                raise
            except httpx.HTTPError:
                metrics.record_outbound("r1", target, method, "error", time.perf_counter() - started)
                raise
            status = getattr(resp, "status_code", None)
            if status is not None:
                tracing.mark_status(client_span, status)
        metrics.record_outbound("r1", target, method, "error" if status is None else metrics.outcome_of(status), time.perf_counter() - started)
        return resp

    def _send(self, send, path: str, method: str = "other", **kwargs) -> httpx.Response:
        # A caller's own headers (for example `Idempotency-Key`, PR-ST-3) ride along with the
        # authorization and correlation headers; the client's own win on a clash.
        extra = kwargs.pop("headers", None) or {}
        kwargs.setdefault("timeout", call_timeout())   # never httpx's implicit 5 s (timeouts.py)
        resp = self._call(send, method, path, {**extra, **self._headers()}, **kwargs)
        if resp.status_code == 401 and self._bearer_token is None:
            # expired or revoked at SME since it was cached: one fresh token, one retry
            resp = self._call(send, method, path, {**extra, **self._headers(refresh=True)}, **kwargs)
        return resp

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._send(httpx.get, path, method="get", **kwargs)

    def post(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response:
        return self._send(httpx.post, path, method="post", json=json, **kwargs)

    def put(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response:
        return self._send(httpx.put, path, method="put", json=json, **kwargs)

    def patch(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response:
        """PATCH through R1 (e.g. the SDK's Intent admin-state update)."""
        return self._send(httpx.patch, path, method="patch", json=json, **kwargs)

    def delete(self, path: str, **kwargs) -> httpx.Response:
        return self._send(httpx.delete, path, method="delete", **kwargs)
