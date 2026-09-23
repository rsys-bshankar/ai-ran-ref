"""Thin HTTP client every module uses to call another module THROUGH R1
Termination, rather than service-to-service directly — matching the
topology every LLD assumes (rApp -> R1 Termination -> backend SMOS) and
extending it to SMO-internal cross-module calls (e.g. SO SMOS dispatching
a CONFIG step to RAN NF OAM, NFO calling FOCOM's QueryInventory).

Phase 1: R1 Termination's routing table (Foundational Platform LLD section 4.2)
maps path prefixes to backend services on the same Docker network, so this
client is a small wrapper over httpx rather than a real service mesh client.
"""

import os

import httpx

R1_GATEWAY_URL = os.environ.get("R1_GATEWAY_URL", "http://r1-termination:8000")


class R1Client:
    def __init__(self, base_url: str = R1_GATEWAY_URL, bearer_token: str | None = None):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def get(self, path: str, **kwargs) -> httpx.Response:
        return httpx.get(self._url(path), headers=self.headers, **kwargs)

    def post(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response:
        return httpx.post(self._url(path), json=json, headers=self.headers, **kwargs)

    def put(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response:
        return httpx.put(self._url(path), json=json, headers=self.headers, **kwargs)

    def delete(self, path: str, **kwargs) -> httpx.Response:
        return httpx.delete(self._url(path), headers=self.headers, **kwargs)
