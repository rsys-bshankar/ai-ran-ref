"""The BFF's southbound side: an OAuth2 client of the SMO, reaching every
module only through R1 Termination.

R1 Termination introspects a Bearer token against SME on every proxied
request (r1-termination/app/main.py's _authorized). The BFF gets its token
exactly the way an rApp does:

  1. GET {R1}/bootstrap -> the tokenEndPoint URI (SME's /oauth2/token);
  2. onboard once as a CAPIF API invoker at SME (/invoker-registrations),
     which mints an apiInvokerId + onboardingSecret — persisted, reused;
  3. client_credentials grant at the token endpoint; the access token is
     cached until shortly before expires_in, and refreshed once on a 401.

Operators never see this token and the browser never talks to R1: GUI
users authenticate to the BFF, the BFF authenticates to the SMO.
"""

import asyncio
import secrets
import time

import httpx

from .db import Database, SmoCredential

_EXPIRY_MARGIN_SECONDS = 30


class SmoAuthError(RuntimeError):
    pass


class R1Gateway:
    def __init__(self, r1_url: str, db: Database, sme_url: str | None = None, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.r1_url = r1_url.rstrip("/")
        self._sme_url_override = sme_url
        self._db = db
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_endpoint: str | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------ token

    async def _discover_token_endpoint(self) -> str:
        if self._token_endpoint:
            return self._token_endpoint
        if self._sme_url_override:
            self._token_endpoint = f"{self._sme_url_override}/oauth2/token"
            return self._token_endpoint
        resp = await self._client.get(f"{self.r1_url}/bootstrap")
        if resp.status_code != 200:
            raise SmoAuthError(f"R1 /bootstrap returned {resp.status_code}")
        for ep in resp.json().get("apiEndpoints", []):
            uri = (ep.get("tokenEndPoint") or {}).get("uri")
            if uri:
                self._token_endpoint = uri
                return uri
        raise SmoAuthError("R1 /bootstrap advertised no tokenEndPoint")

    def _sme_base(self, token_endpoint: str) -> str:
        return self._sme_url_override or token_endpoint.rsplit("/oauth2/token", 1)[0]

    async def _onboard_invoker(self, token_endpoint: str) -> SmoCredential:
        # SME stores apiInvokerPublicKey but never verifies anything against
        # it (its token endpoint checks only the onboarding secret), so an
        # opaque per-BFF identifier is the honest value here, not a key.
        resp = await self._client.post(f"{self._sme_base(token_endpoint)}/invoker-registrations",
                                       json={"apiInvokerPublicKey": f"smo-gui-bff:{secrets.token_urlsafe(16)}"})
        if resp.status_code != 201:
            raise SmoAuthError(f"SME invoker onboarding returned {resp.status_code}")
        body = resp.json()
        with self._db.session() as s:
            cred = s.get(SmoCredential, 1) or SmoCredential(id=1)
            cred.api_invoker_id, cred.onboarding_secret = body["apiInvokerId"], body["onboardingSecret"]
            s.merge(cred)
            s.commit()
        return cred

    def _stored_credential(self) -> SmoCredential | None:
        with self._db.session() as s:
            return s.get(SmoCredential, 1)

    async def _request_token(self, token_endpoint: str, cred: SmoCredential) -> httpx.Response:
        return await self._client.post(token_endpoint, json={
            "grant_type": "client_credentials", "client_id": cred.api_invoker_id,
            "client_secret": cred.onboarding_secret, "scope": "smo-gui",
        })

    async def token(self, force_refresh: bool = False) -> str:
        async with self._lock:
            if not force_refresh and self._token and time.time() < self._token_expires_at:
                return self._token
            try:
                token_endpoint = await self._discover_token_endpoint()
                cred = self._stored_credential() or await self._onboard_invoker(token_endpoint)
                resp = await self._request_token(token_endpoint, cred)
                if resp.status_code == 400:
                    # SME no longer knows this invoker (e.g. its DB was
                    # reset): onboard afresh, once.
                    cred = await self._onboard_invoker(token_endpoint)
                    resp = await self._request_token(token_endpoint, cred)
            except httpx.HTTPError as exc:
                raise SmoAuthError(f"SME unreachable: {exc.__class__.__name__}") from exc
            if resp.status_code != 200:
                raise SmoAuthError(f"SME token endpoint returned {resp.status_code}")
            body = resp.json()
            self._token = body["access_token"]
            self._token_expires_at = time.time() + max(int(body.get("expires_in", 60)) - _EXPIRY_MARGIN_SECONDS, 1)
            return self._token

    # ------------------------------------------------------------ calls

    async def request(self, method: str, path: str, *, params=None, content: bytes | None = None,
                      headers: dict | None = None, timeout: float | None = None) -> httpx.Response:
        """One call to R1 Termination at `path` (already /<module>/...),
        with the BFF's Bearer token. Retried once with a fresh token if R1
        answers 401 (token expired or revoked at SME).
        """
        url = f"{self.r1_url}{path}"
        kwargs = {"params": params, "content": content}
        if timeout is not None:
            kwargs["timeout"] = timeout
        for attempt in (0, 1):
            token = await self.token(force_refresh=attempt == 1)
            resp = await self._client.request(method, url, headers={**(headers or {}), "Authorization": f"Bearer {token}"}, **kwargs)
            if resp.status_code != 401:
                return resp
        return resp

    async def r1_health(self, timeout: float) -> httpx.Response:
        return await self._client.get(f"{self.r1_url}/health", timeout=timeout)
