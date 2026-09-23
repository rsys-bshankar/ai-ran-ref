"""A1 Termination — the southbound client component inside A1 Related SMOS.

SMO Design v1.3 section 3.9's topology: R1 Termination -> A1 Policy
Functions -> A1 Termination [TLS+mTLS+OAuth2.0+JWT, HTTP/1.1 mandatory,
HTTP/2 recommended, JSON, per A1TP] -> Near-RT RIC. "A1 Termination" is
an internal southbound component of A1 Related SMOS, not a separate SMO
module — kept as its own client file since it's a distinct trust
boundary from R1Client (R1Client talks to other SMO modules through R1
Termination; this talks OUT of the SMO framework entirely, to a
Near-RT RIC).

Phase 1: the mock Near-RT RIC (../mock-near-rt-ric) is the only thing on
the other end, on an isolated network segment with no other module able
to reach it (closes RT-7). A1TP's real transport requirements
(TLS+mTLS+OAuth2.0+JWT) are noted, not implemented — this is a plain
HTTP client against a test double, not an A1AP-conformant stack.
"""

import os

import httpx

MOCK_NEAR_RT_RIC_URL = os.environ.get("MOCK_NEAR_RT_RIC_URL", "http://mock-near-rt-ric:8000")


class A1TerminationClient:
    def __init__(self, base_url: str = MOCK_NEAR_RT_RIC_URL):
        self.base_url = base_url

    def create_policy(self, near_rt_ric_id: str, policy_type_id: str, policy_object: dict) -> dict:
        resp = httpx.post(f"{self.base_url}/a1-p/policies", params={
            "near_rt_ric_id": near_rt_ric_id, "policy_type_id": policy_type_id,
        }, json=policy_object, timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    def update_policy(self, policy_id: str, policy_object: dict) -> dict:
        resp = httpx.put(f"{self.base_url}/a1-p/policies/{policy_id}", json=policy_object, timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    def delete_policy(self, policy_id: str) -> None:
        httpx.delete(f"{self.base_url}/a1-p/policies/{policy_id}", timeout=5.0)

    def query_policy_status(self, policy_id: str) -> dict:
        """The cache/pass-through duality (A1 Related LLD section 1.1): a
        live Near-RT RIC call, used here to refresh the local mirror
        rather than trusting the cached enforcement_status indefinitely.
        """
        resp = httpx.get(f"{self.base_url}/a1-p/policies/{policy_id}/status", timeout=5.0)
        resp.raise_for_status()
        return resp.json()
