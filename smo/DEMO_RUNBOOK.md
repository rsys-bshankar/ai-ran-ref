# Demo runbook — a sample rApp's full lifecycle

A step-by-step walkthrough of `smo/docs/call-flows/01-rapp-onboarding-to-deployment.md`
against a real, running `docker compose up` stack — onboard a real
package, deploy it, simulate its bootstrap (SME/DME registration,
OAuth2), watch it go RUNNING, then retire it. Every request body below
is real — copy-pasteable, not illustrative — and every response shape
matches this build's actual routes.

**This must be run in your own environment with a real Docker daemon.**
Nothing in this repo's own sandbox can run `docker compose up` (no
Docker daemon there — `OPEN_ITEMS.md` section 2's own documented
elision); this runbook is what you run instead of a live demo I can
run for you.

## What you'll onboard

`smo/samples/hello-world-rapp.csar` — a real, valid CSAR package
(`TOSCA-Metadata/TOSCA.meta`, `Definitions/asd.yaml`,
`Files/Acm/definition/compositions.json`, a real Helm chart artifact,
plus reference SME/DME registration bodies under `Files/Sme/`/`Files/Dme/`),
adapted from the real O-RAN-SC reference's own sample package
(`nonrtric-plt-rappmanager/sample-rapp-generator/rapp-all`) to satisfy
this build's own `Onboarding` validator. See `smo/samples/build_csar.py`
if you need to rebuild it after editing `smo/samples/hello-world-rapp/`.
Proven to onboard and deploy for real (not just described) by
`tests_integration/test_cross_service.py::test_real_demo_csar_onboards_and_deploys`.

## Why every step below runs via `docker compose exec`

Only `r1-termination` publishes a host port (`8080:8000`, per
`docker-compose.yml`) — every other service (`onboarding`, `sme`, `dme`,
`nfo`, `rapp-mgmt`, ...) is reachable only from *inside* the compose
network, by container hostname. Rather than editing `docker-compose.yml`
to publish more ports, every command below runs Python (already
installed in every container, `httpx` included) via
`docker compose exec r1-termination python3 -c "..."` — that container is
always up once `docker compose up` succeeds, and can already reach every
other service by hostname the same way real inter-service calls do.

## 0. Start the stack

```bash
cd smo
docker compose up -d --build
docker compose ps   # confirm all services are healthy/running
```

## 1. Serve the sample package on the compose network

`Onboarding`'s real validator fetches the package over HTTP (it never
reads local files) — so it needs a real URL reachable from inside the
network:

```bash
docker compose cp samples/hello-world-rapp.csar r1-termination:/tmp/hello-world-rapp.csar
docker compose exec -d r1-termination python3 -m http.server 8899 --directory /tmp
```

`http://r1-termination:8899/hello-world-rapp.csar` is now reachable from
every other container by hostname.

## 2. Onboard the package

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://onboarding:8000/packages', json={
    'location': 'http://r1-termination:8899/hello-world-rapp.csar',
})
print(r.status_code, r.json())
"
```

Note the `packageId` in the response. Poll until it reaches `AVAILABLE`
(should be immediate — there's no async worker, `OnboardPackage` resolves
synchronously):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://onboarding:8000/packages/<packageId>/onboarding-status')
print(r.json())
"
```

`state` should be `AVAILABLE` and `nfDeploymentDescriptorId` should be a
real UUID (NFO's real `CreateDescriptor`, called automatically once
validation passes). If `state` is `FAILED` instead, something about the
CSAR itself is malformed — re-run `python3 smo/samples/build_csar.py`
and re-copy it (step 1), and check `Files/Acm/definition/compositions.json`
and `TOSCA-Metadata/TOSCA.meta`/`Entry-Definitions` are both present.

## 3. Deploy it (CreateInstance)

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://rapp-mgmt:8000/instances', json={
    'packageId': '<packageId>', 'config': {},
})
print(r.status_code, r.json())
"
```

Note the `instanceId` and `oauthClientId` (this build's rAppId, per
Foundational Platform LLD section 1 — every subsequent SME/DME call
below uses it). Internally this already called NFO's real
`Instantiate`, which queried FOCOM's real `/inventory` for a cluster —
check it:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://focom:8000/inventory')
print(r.json())
"
```

The instance is now `DEPLOYING` — waiting for the (simulated) container
to bootstrap and call back.

## 4. Simulate the deployed container's bootstrap

This is the one step reachable from your own host, since it's the one
R1 Termination call a real rApp container makes before it has a token:

```bash
curl -s http://localhost:8080/bootstrap | python3 -m json.tool
```

Real response shape: `apiEndpoints` naming `service-apis` and
`published-apis`, each with a `tokenEndPoint`/`apiEndPoint` pointing
directly at SME (bypassing R1's own auth gate entirely — necessary,
since R1 requires a bearer token on every other proxied route, and the
container doesn't have one yet).

From here on, continue with `docker compose exec r1-termination` again
— those URIs are container-internal hostnames.

**Register as a provider (APF)** — `Files/Sme/providers/provider.json`'s body:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/provider-registrations', json={
    'apfId': 'hello-world-rapp', 'providerDomainInfo': 'Hello World rApp — demo provider domain',
})
print(r.status_code, r.json())
"
```

**Register as an invoker** — `Files/Sme/invokers/invoker.json`'s body:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/invoker-registrations', json={
    'apiInvokerId': 'hello-world-rapp', 'onboardingSecret': 'demo-onboarding-secret-change-me',
})
print(r.status_code, r.json())
"
```

**Worth calling out live**: the client picks its own `apiInvokerId` and
`onboardingSecret` here. The real CAPIF core's invoker-onboarding flow
is public-key-based — the client submits a public key, and CAPIF
*generates* both values server-side and hands them back; `apiInvokerId`
"shall not be present" in the real onboarding request at all. This
build's Phase 1 is the weaker, self-asserted model, with no equivalent
of the real CAPIF core's separate "Trusted Invokers" security-context
registry behind it either (`SPEC_AUDIT.md`'s SME section, items 1 and
2). Named here rather than left silent, since this exact step is where
it's visible.

**Obtain an OAuth2 token:**

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/oauth2/token', json={
    'grant_type': 'client_credentials', 'client_id': 'hello-world-rapp',
    'client_secret': 'demo-onboarding-secret-change-me',
})
print(r.status_code, r.json())
"
```

**Publish the service API** — `Files/Sme/serviceapis/api-set.json`'s body:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/published-apis/v1/hello-world-rapp/service-apis', json={
    'serviceName': 'helloworld-api', 'producerId': 'hello-world-rapp',
    'endpoint': 'http://hello-world-rapp:8080/helloworld/v1', 'version': 'v1',
    'fullApiVersions': ['v1'], 'moduleScope': 'hello-world-rapp',
})
print(r.status_code, r.json())
"
```

**Register as a DME producer (optional)** — `Files/Dme/infoproducers/producer.json`'s body:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://dme:8000/production-capabilities', json={
    'namespace': 'demo', 'name': 'hello-world-metrics', 'version': '1.0',
    'typeName': 'hello-world-metrics-v1', 'producerId': 'hello-world-rapp',
    'dataProductionSchema': {'type': 'object', 'properties': {'greeting': {'type': 'string'}}},
    'producerHealthCallbackUrl': 'http://hello-world-rapp:8080/health',
    'jobCallbackUrl': 'http://hello-world-rapp:8080/dme-jobs',
})
print(r.status_code, r.json())
"
```

## 5. Complete bootstrap — DEPLOYING → RUNNING

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://rapp-mgmt:8000/instances/<instanceId>/bootstrap-complete')
print(r.status_code, r.json())
"
```

`state` should now be `RUNNING`. Confirm:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://rapp-mgmt:8000/instances/<instanceId>')
print(r.json())
"
```

## 6. Operate (optional) — a performance/fault report

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://rapp-mgmt:8000/instances/<instanceId>/performance', json={'greeting': 'hello world', 'requestsServed': 1})
print(r.status_code, r.json())
"
```

## 7. Retire it — Terminate, then Delete

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://rapp-mgmt:8000/instances/<instanceId>/terminate')
print(r.status_code, r.json())
"
```

`state` should be `UNDEPLOYED`. Then the real, separate delete:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://rapp-mgmt:8000/instances/<instanceId>')
print(r.status_code)
"
```

204 with an empty body — the instance row is gone. The full lifecycle
— onboard, deploy, bootstrap, register, operate, retire — is now
complete against a real running stack.

## Known rough edges for a live walkthrough

- `smo/docs/call-flows/01-rapp-onboarding-to-deployment.md`'s own
  diagram doesn't show the invoker/provider registration steps above
  (steps predate the OAuth2 work that added them) — this runbook is the
  more current, accurate version; worth updating that diagram to match.
- `smo_shared.r1_client.R1Client`, used internally by `Onboarding`,
  `rApp Mgmt`, and `NFO`, calls other services directly by container
  hostname (not through R1 Termination's own proxy) — matching
  `tests_integration/mesh.py`'s own documented scope choice that
  cross-module calls bypass the gateway. Nothing above depends on this,
  but it's why steps 2-3 above don't need a bearer token even though
  they're server-to-server calls.
- `smo/SPEC_AUDIT.md` lists several small, real spec-conformance gaps
  (e.g. RAN NF OAM's alarm model, FOCOM's `ResourceType` fields) — none
  are on this lifecycle's critical path, so none should visibly break
  this walkthrough, but flag it here if one does.
