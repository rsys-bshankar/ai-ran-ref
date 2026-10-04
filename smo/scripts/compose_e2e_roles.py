"""PR-SEC-14: caller roles through the real gateway, on the running compose stack.

    docker compose exec -T sme python3 - < scripts/compose_e2e_roles.py

Runs inside the SME container, which holds the enrollment secret (`/run/secrets/enrollment_secret`) the way every SMO module does. It registers
one invoker with the secret (an SMO module) and one without (an rApp), takes a token for each, and calls the gateway as both:

  - SME records the two kinds, and refuses an rApp the internal scope;
  - an rApp is refused (403) on the internal-only routes and the backend is not reached; an SMO module is not;
  - the limit an SMO module sets for the rApp's invoker id is the one that stops the rApp's config jobs (AI-10.2), through the real
    R1 -> RAN NF OAM path, with the invoker id R1 vouches for.

Exits 1 on the first group of failures; removes what it made.
"""

import sys

import httpx

SME, R1 = "http://sme:8000", "http://r1-termination:8000"
SECRET = open("/run/secrets/enrollment_secret", encoding="utf-8").read().strip()
failures: list[str] = []
made: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


def register(enrolled: bool) -> dict:
    resp = httpx.post(f"{SME}/invoker-registrations", json={"apiInvokerPublicKey": "e2e-roles"},
                      headers={"X-SMO-Enrollment": SECRET} if enrolled else {}, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    made.append(body["apiInvokerId"])
    return body


def token(reg: dict, scope: str) -> httpx.Response:
    return httpx.post(f"{SME}/oauth2/token", json={"grant_type": "client_credentials", "client_id": reg["apiInvokerId"],
                                                   "client_secret": reg["onboardingSecret"], "scope": scope}, timeout=10)


def call(method: str, path: str, access: str, **kw) -> httpx.Response:
    return httpx.request(method, f"{R1}{path}", headers={"Authorization": f"Bearer {access}"}, timeout=30, **kw)


try:
    module, rapp = register(True), register(False)
    check("an invoker that presents the enrollment secret is recorded as internal", module.get("role") == "internal", module)
    check("an invoker that does not is recorded as an rApp", rapp.get("role") == "rapp", rapp)
    check("an rApp is refused the internal scope", token(rapp, "smo-internal").status_code == 400)
    internal_access = token(module, "smo-internal")
    rapp_access = token(rapp, "smo-rapp")
    check("a module gets the internal scope and an rApp its own", internal_access.status_code == 200 and rapp_access.status_code == 200)
    if internal_access.status_code == 200 and rapp_access.status_code == 200:
        internal_tok, rapp_tok = internal_access.json()["access_token"], rapp_access.json()["access_token"]
        probe = f"/ran-nf-oam/rapp-limits/{rapp['apiInvokerId']}"

        r = call("PUT", probe, rapp_tok, json={"maxConfigJobsPerHour": 1})
        check("an rApp cannot set a limit (403 ROLE_NOT_PERMITTED)", r.status_code == 403 and r.json().get("title") == "ROLE_NOT_PERMITTED", (r.status_code, r.text[:200]))
        check("the refused call changed nothing", call("GET", probe, internal_tok).status_code == 404)
        for method, path in (("DELETE", probe), ("PUT", "/ran-nf-oam/kpi-definitions/e2e"), ("POST", "/ran-nf-oam/config-history/purge")):
            r = call(method, path, rapp_tok, json={} if method != "DELETE" else None)
            check(f"an rApp cannot {method} {path.split('/', 2)[2].split('/')[0]}", r.status_code == 403, (r.status_code, r.text[:120]))
        r = call("GET", "/ran-nf-oam/kpi-definitions", rapp_tok)
        check("an rApp can still read what is open to it", r.status_code == 200, r.status_code)

        r = call("PUT", probe, internal_tok, json={"maxConfigJobsPerHour": 1})
        check("an SMO module can set the limit", r.status_code == 200 and r.json().get("maxConfigJobsPerHour") == 1, (r.status_code, r.text[:200]))

        job = {"requestedBy": "e2e-roles", "scope": "cell", "changes": [{"managedElementRef": "e2e-no-such-element", "attributeChanges": {"x": 1}}]}
        first = call("POST", "/ran-nf-oam/config-jobs", rapp_tok, json=job)
        second = call("POST", "/ran-nf-oam/config-jobs", rapp_tok, json=job)
        check("the rApp's first config job is accepted", first.status_code in (200, 202), (first.status_code, first.text[:200]))
        check("its second is refused by the limit set for its own invoker id (429)", second.status_code == 429, (second.status_code, second.text[:200]))
        other = call("POST", "/ran-nf-oam/config-jobs", internal_tok, json=job)
        check("the limit does not touch a caller it was not set for", other.status_code in (200, 202), (other.status_code, other.text[:200]))

        r = call("DELETE", probe, internal_tok)
        check("an SMO module can remove it", r.status_code == 204, r.status_code)
finally:
    for invoker in made:
        try:
            httpx.delete(f"{SME}/invoker-registrations/{invoker}", timeout=10)
        except httpx.HTTPError:
            pass

print()
if failures:
    print(f"{len(failures)} role check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("all role checks passed")
