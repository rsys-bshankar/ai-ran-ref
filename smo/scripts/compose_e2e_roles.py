"""PR-SEC-14: caller roles through the real gateway, on the running compose stack.

    docker compose exec -T sme python3 - < scripts/compose_e2e_roles.py

Runs inside the SME container, which holds the enrollment secret (`/run/secrets/enrollment_secret`) the way every SMO module does. It registers
one invoker with the secret (an SMO module) and one without (an rApp), takes a token for each, and calls the gateway as both:

  - SME records the two kinds, and refuses an rApp the internal scope;
  - an rApp is refused (403) on the internal-only routes and the backend is not reached; an SMO module is not;
  - the limit an SMO module sets for the rApp's invoker id is the one that stops the rApp's config jobs (AI-10.2), through the real
    R1 -> RAN NF OAM path, with the invoker id R1 vouches for.

Every check runs and the script exits 1 at the end if any failed; the invokers it registered are deleted in a `finally`.
"""

import sys

import time

import httpx

SME, R1 = "http://sme:8000", "http://r1-termination:8000"
SECRET = open("/run/secrets/enrollment_secret", encoding="utf-8").read().strip()
failures: list[str] = []
made: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    """Prints one `ok` / `FAIL` line and remembers a failure; the script goes on and lists every failure at the end."""
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


def register(enrolled: bool) -> dict:
    """Registers an invoker with SME, with the enrollment secret when `enrolled` (an SMO module) and without it (an rApp), and returns SME's answer.

        The invoker id is added to `made` so the final cleanup deletes it. Raises `httpx.HTTPStatusError` when SME refuses.
    """
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

        # AI-10.3: the blast radius the platform holds an rApp to (checked before anything is sent, so it needs no element on the stack)
        r = call("PUT", probe, internal_tok, json={"maxElementsPerJob": 1})
        check("an SMO module can set a blast radius", r.status_code == 200 and r.json().get("maxElementsPerJob") == 1, (r.status_code, r.text[:200]))
        wide = {"requestedBy": "e2e-roles", "scope": "cell", "changes": [
            {"managedElementRef": "e2e-no-such-element-a", "attributeChanges": {"x": 1}}, {"managedElementRef": "e2e-no-such-element-b", "attributeChanges": {"x": 1}}]}
        r = call("POST", "/ran-nf-oam/config-jobs", rapp_tok, json=wide)
        check("a job over the rApp's blast radius is refused (403)", r.status_code == 403 and "RAPP_BLAST_RADIUS_EXCEEDED" in r.text, (r.status_code, r.text[:200]))
        check("the same job from an SMO module is not", call("POST", "/ran-nf-oam/config-jobs", internal_tok, json=wide).status_code in (200, 202))
        check("a limit that names none is refused (422)", call("PUT", probe, internal_tok, json={}).status_code == 422)
        call("DELETE", probe, internal_tok)

        # AI-10.4: the kill switch
        kill = f"/ran-nf-oam/rapp-kill/{rapp['apiInvokerId']}"
        r = call("PUT", kill, rapp_tok, json={"requestedBy": "e2e"})
        check("an rApp cannot stop an rApp (403)", r.status_code == 403, (r.status_code, r.text[:120]))
        r = call("PUT", kill, internal_tok, json={"requestedBy": "e2e-roles", "reason": "end-to-end check"})
        check("an SMO module can stop one", r.status_code == 200, (r.status_code, r.text[:200]))
        r = call("POST", "/ran-nf-oam/config-jobs", rapp_tok, json=job)
        check("a stopped rApp's config job is refused (403 RAPP_KILLED)", r.status_code == 403 and "RAPP_KILLED" in r.text, (r.status_code, r.text[:200]))
        check("a caller that was not stopped is not affected", call("POST", "/ran-nf-oam/config-jobs", internal_tok, json=job).status_code in (200, 202))
        # AI-10.6: every refusal is recorded, and only SMO modules may read the record
        r = call("GET", f"/ran-nf-oam/safeguard-refusals?invoker_id={rapp['apiInvokerId']}", internal_tok)
        codes = [i["refusal"] for i in r.json().get("items", [])] if r.status_code == 200 else []
        check("the refusals of the rApp were recorded (blast radius, then the kill switch)", "RAPP_KILLED" in codes and "RAPP_BLAST_RADIUS_EXCEEDED" in codes, (r.status_code, codes))
        check("an rApp cannot read the record of refusals (403)", call("GET", "/ran-nf-oam/safeguard-refusals", rapp_tok).status_code == 403)
        check("an rApp cannot list who is told about them (403)", call("GET", "/ran-nf-oam/safeguard-subscriptions", rapp_tok).status_code == 403)
        r = call("POST", "/rapp-mgmt/instances", rapp_tok, json={})
        check("an rApp cannot change what it does not use: creating an instance is 403 ROLE_NOT_PERMITTED", r.status_code == 403 and r.json().get("title") == "ROLE_NOT_PERMITTED", (r.status_code, r.text[:200]))
        r = call("POST", "/dme/data-jobs", rapp_tok, json={})
        # this rApp is stopped at this point, and whether a gateway replica has noticed yet depends on its cache (3 s) and on how many replicas
        # there are, so what is asked is only that the role policy does not refuse it; "stopped" is the next check's business
        check("what it does use is not refused by the role policy (a DME data job is not 403 ROLE_NOT_PERMITTED)", "ROLE_NOT_PERMITTED" not in r.text, (r.status_code, r.text[:200]))
        # the gateway refuses every change of a stopped rApp, not only its config jobs (it re-reads the switch every 3 s)
        time.sleep(4)
        r = call("POST", "/dme/data-jobs", rapp_tok, json={})
        check("a stopped rApp's change anywhere is refused at the gateway (403 RAPP_KILLED)", r.status_code == 403 and "RAPP_KILLED" in r.text, (r.status_code, r.text[:200]))
        r = call("DELETE", "/dme/data-jobs/e2e-none", rapp_tok)
        check("a stopped rApp may still withdraw (a DELETE is not refused as stopped)", "RAPP_KILLED" not in r.text, (r.status_code, r.text[:200]))
        r = call("DELETE", kill, rapp_tok)
        check("an rApp cannot lift it (403)", r.status_code == 403, r.status_code)
        r = call("DELETE", kill, internal_tok)
        check("an SMO module can lift it", r.status_code == 204, r.status_code)
        time.sleep(4)          # the gateway re-reads the switch every 3 s: wait for it to see the lift
        r = call("POST", "/dme/data-jobs", rapp_tok, json={})
        check("once lifted the rApp's change is no longer refused as stopped", "RAPP_KILLED" not in r.text, (r.status_code, r.text[:200]))
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
