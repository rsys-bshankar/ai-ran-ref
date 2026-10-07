#!/usr/bin/env python3
"""Prints an SMO access token for the authenticated DAST scan (PR-V-7), on stdout and nothing else.

Run inside the `sme` container of the compose stack (`docker compose exec -T sme python - rapp < scripts/dast_token.py`):

    rapp       an invoker registered without the enrollment secret: what an rApp is, and the role the gateway restricts
    internal   one registered with it (/run/secrets/enrollment_secret): an SMO module, which the gateway lets through to every route

The invoker is a throwaway of the scan's stack; the token lives an hour (the scan takes minutes).
"""

import sys

import httpx

SME = "http://sme:8000"
role = sys.argv[1] if len(sys.argv) > 1 else "rapp"
if role not in ("rapp", "internal"):
    sys.exit(f"role must be rapp or internal, not {role!r}")
headers = {}
if role == "internal":
    headers["X-SMO-Enrollment"] = open("/run/secrets/enrollment_secret", encoding="utf-8").read().strip()
reg = httpx.post(f"{SME}/invoker-registrations", json={"apiInvokerPublicKey": f"dast-{role}"}, headers=headers, timeout=10)
reg.raise_for_status()
invoker = reg.json()
tok = httpx.post(f"{SME}/oauth2/token", json={"grant_type": "client_credentials", "client_id": invoker["apiInvokerId"],
                                              "client_secret": invoker["onboardingSecret"]}, timeout=10)
tok.raise_for_status()
print(tok.json()["access_token"])
