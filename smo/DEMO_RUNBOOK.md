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

**Register as an invoker** — `Files/Sme/invokers/invoker.json`'s body.
`SPEC_AUDIT.md`'s SME item 1: the real CAPIF core's onboarding is
public-key-based — the client submits `apiInvokerPublicKey`, and CAPIF
*generates* `apiInvokerId`/`onboardingSecret` server-side and hands
them back (`apiInvokerId` "shall not be present" in the real request
at all). This build now matches that: the client supplies only its
own public key, not a self-asserted identity or a client-chosen
secret:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/invoker-registrations', json={'apiInvokerPublicKey': 'demo-rapp-public-key'})
print(r.status_code, r.json())
"
```

Note the returned `apiInvokerId` and `onboardingSecret` — every
subsequent SME call below uses them. (Real CAPIF core's separate
"Trusted Invokers" security-context registry still has no equivalent
here — `SPEC_AUDIT.md`'s SME item 2, a genuine additional subsystem,
not just this onboarding-flow gap.)

**Obtain an OAuth2 token:**

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/oauth2/token', json={
    'grant_type': 'client_credentials', 'client_id': '<apiInvokerId>',
    'client_secret': '<onboardingSecret>',
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

## 7. RAN NF OAM closed-loop (optional) — a real CM write and fault lifecycle

Independent of the sample rApp instance above — this shows the
platform's own RAN-facing capability: a managed RAN function actually
being reconfigured and reporting a fault, the core "AI-RAN" story.
Register a managed element behind the mock O1 Adaptor (this build's own
NETCONF-shaped test double for a real O1 network element,
`mock-o1-adaptor:8000/edit-config` — `docker-compose.yml`'s own comment
on that service names this exact gap: no ME had ever been registered
against it until now):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ran-nf-oam:8000/o1-adaptor-endpoints', json={
    'managedElementRef': 'demo-o-du-1', 'adaptorUri': 'http://mock-o1-adaptor:8000/edit-config',
    'protocolSupport': ['NETCONF'], 'o1Protocol': 'NETCONF', 'entityType': 'O-DU',
})
print(r.status_code, r.json())
"
```

Note the returned `endpointId` — health starts `DISCOVERED`. Heartbeat
it to `ACTIVE` (a real O1 Adaptor would do this on its own timer):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ran-nf-oam:8000/o1-adaptor-endpoints/<endpointId>/heartbeat')
print(r.status_code, r.json())
"
```

**Dispatch a real CM write** — `WriteConfigurationChanges` decomposes
this into a real NETCONF `<edit-config>` RPC sent to the mock O1
Adaptor (`netconf_client.py`), not a stub:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ran-nf-oam:8000/config-jobs', json={
    'requestedBy': 'hello-world-rapp', 'scope': 'cell',
    'changes': [{'managedElementRef': 'demo-o-du-1', 'attributeChanges': {'adminState': 'UNLOCKED'}}],
})
print(r.status_code, r.json())
"
```

Note the `jobId`. Confirm the sub-change actually reached the mock O1
Adaptor and applied:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://ran-nf-oam:8000/config-jobs/<jobId>')
print(r.status_code, r.json())
r2 = httpx.get('http://mock-o1-adaptor:8000/edit-config/demo-o-du-1')
print(r2.status_code, r2.json())
"
```

`status` should be `COMPLETED`, the sub-change `APPLIED`, and the mock
adaptor's own record shows the real applied attribute change.

**A real partial failure** — `WriteConfigurationChanges` decomposes a
multi-ME request into independent per-ME sub-changes and aggregates
their outcomes (RAN NF OAM LLD section 5.1); a batch touching one
healthy, registered ME and one ME that was never registered genuinely
settles as `PARTIAL_SUCCESS`, not an all-or-nothing failure:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ran-nf-oam:8000/config-jobs', json={
    'requestedBy': 'hello-world-rapp', 'scope': 'cell',
    'changes': [
        {'managedElementRef': 'demo-o-du-1', 'attributeChanges': {'adminState': 'LOCKED'}},
        {'managedElementRef': 'demo-o-du-2-never-registered', 'attributeChanges': {'adminState': 'LOCKED'}},
    ],
})
print(r.status_code, r.json())
r2 = httpx.get(f'http://ran-nf-oam:8000/config-jobs/{r.json()[\"jobId\"]}')
print(r2.status_code, r2.json())
"
```

The job's own `status` is `PARTIAL_SUCCESS`; `subChanges` shows
`demo-o-du-1` genuinely `APPLIED` (the real NETCONF RPC fired again,
setting `adminState` back to `LOCKED`) alongside
`demo-o-du-2-never-registered` `REJECTED` with `rejectionReason:
ENDPOINT_UNREACHABLE` — no `O1AdaptorEndpoint` was ever registered for
it, the same real per-ME dispatch gate the closed-loop steps above
already went through successfully. This is the honest operational case
a bulk RAN configuration push actually hits (one node in a batch is
down or never onboarded), not a scripted failure.

**Raise, acknowledge, and clear a fault alarm** on the same ME:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ran-nf-oam:8000/alarms/ingest', params={
    'source_alarm_id': 'demo-alarm-1', 'managed_element_ref': 'demo-o-du-1',
    'severity': 'major', 'alarm_type': 'EQUIPMENT_ALARM',
})
print(r.status_code, r.json())
"
```

Note the `alarmId`, then acknowledge and clear it:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.patch('http://ran-nf-oam:8000/alarms/<alarmId>/ack', params={'new_state': 'ACKNOWLEDGED', 'ack_user_id': 'demo-operator'})
print(r.status_code, r.json())
r2 = httpx.patch('http://ran-nf-oam:8000/alarms/<alarmId>/clear', params={'clear_user_id': 'demo-operator'})
print(r2.status_code, r2.json())
"
```

`severity` on the cleared alarm should read `cleared`, with
`ackUserId`/`clearUserId`/`changedAt` all populated — a full fault
lifecycle against real, persisted rows, not a mock.

## 8. FOCOM resource management (optional) — provision, subscribe, observe a real notification

Independent of the sample rApp instance above — this shows FOCOM's O2IMS
inventory-subscription mechanism firing for real: subscribe to inventory
changes for a resource type, then provision and deprovision a resource
of that type and see each one actually attempted against the
subscriber's callback.

Subscribe first, filtered to a resource type this walkthrough will use:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://focom:8000/inventory/subscriptions', json={
    'callback': 'http://demo-consumer:9000/inventory-events',
    'resourceTypeId': 'gpu-l40', 'consumerSubscriptionId': 'demo-sub-1',
})
print(r.status_code, r.json())
"
```

Note the `subscriptionId`. Provision a matching resource:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://focom:8000/resources/provision', json={'resourceTypeId': 'gpu-l40', 'description': 'demo GPU node'})
print(r.status_code, r.json())
"
```

`resourceId` in the response is a real, persisted `Resource` row —
confirm it with the pool drill-down:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://focom:8000/resource-pools/pool-0/resources')
print(r.status_code, r.json())
"
```

Provisioning fired a real `CREATE` notification at `_notify_inventory_
subscribers` — `focom`'s own logs show the delivery attempt to
`http://demo-consumer:9000/inventory-events` (there's no real listener
container in this compose stack at that address, so the attempt fails
DNS resolution and is silently dropped — delivery is deliberately
best-effort, the same behavior `test_inventory_notification_delivery_
survives_unreachable_subscriber` proves won't ever surface as a 500 to
the caller). `tests_integration/test_demo_runbook.py` proves the outbound
call itself — method, URL, and body — really fires, by intercepting it at
the same `httpx.post` call FOCOM's own code makes, rather than
re-implementing the notification logic.

Deprovision it — this fires a matching `DELETE` notification the same way:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://focom:8000/resources/<resourceId>')
print(r.status_code, r.json())
"
```

## 9. Policy Mgmt intent automation (optional) — register, dispatch, retract

Independent of the sample rApp instance above — this shows Policy Mgmt's
real Intent-to-RMIH dispatch mechanism firing: an SMO-internal RAN
Management Intent Handler (RMIH) declares what it can fulfil, an rApp
expresses an Intent, and Policy Mgmt matches and notifies the right RMIH
automatically.

Register an RMIH. Per D-SEC-POLICY-1, only an SMO-internal module may
hold an `rmihId` — an rApp UUID is rejected — so this uses `so-smos`,
the same identity SO SMOS registers under in the real deployment:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://policy-mgmt:8000/intent-handling-functions', json={
    'rmihId': 'so-smos', 'smeServiceId': 'so-smos-svc',
    'capabilities': [{'supportedExpectationObjectType': 'RAN_SUBNETWORK'}],
    'notificationCallbackUri': 'http://so-smos:8000/intents/notify',
    'intentHandlingScope': ['RAN'],
})
print(r.status_code, r.json())
"
```

Create an Intent whose `expectationObject.objectType` matches that
RMIH's declared capability (`TS28312_IntentNrm.yaml`'s own field — not
an invented top-level type string):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://policy-mgmt:8000/intents', json={
    'expectations': [{'expectationObject': {'objectType': 'RAN_SUBNETWORK'}}],
    'rmioId': 'hello-world-rapp', 'intentHandlingScope': 'RAN',
})
print(r.status_code, r.json())
"
```

`CreateIntent` matched the Intent's requested `RAN_SUBNETWORK` object
type against every registered RMIH's declared capabilities (pre-filtered
by `intentHandlingScope`) and dispatched a real notification to
`so-smos`'s own callback — `so-smos:8000/intents/notify` has no route
that accepts it yet (dispatch is deliberately best-effort, same pattern
as FOCOM's inventory notifications above), so watch `policy-mgmt`'s own
logs for the attempted delivery. `tests_integration/test_demo_runbook.py`
proves the real dispatch fires with the correct `intentId`/
`expectationObjectTypes` payload, by intercepting the exact `httpx.post`
call `create_intent` makes.

Note the `intentId`, then confirm the persisted Intent:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://policy-mgmt:8000/intents/<intentId>')
print(r.status_code, r.json())
"
```

Retract the Intent, then deregister the RMIH — symmetric teardown,
same pattern as FOCOM's provision/deprovision above:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://policy-mgmt:8000/intents/<intentId>')
print(r.status_code)
r2 = httpx.delete('http://policy-mgmt:8000/intent-handling-functions/so-smos')
print(r2.status_code)
"
```

## 10. A1 Policy Management (optional) — register, enforce, a real duplicate rejection, retract

Independent of the sample rApp instance above — this exercises a whole
module the runbook has never touched: A1 Related's real mapping-store
role, a genuine round trip to the mock Near-RT RIC, and its real
duplicate-policy-content rejection.

Register as a supervised service (per `pms-api-v3.json`'s
`putService`; `keepAliveIntervalSeconds: 0` disables supervision for
this walkthrough — a positive value would need repeated keepalive
calls or the service gets swept and its policies torn down):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.put('http://a1-related:8000/services', json={'serviceId': 'hello-world-rapp', 'keepAliveIntervalSeconds': 0})
print(r.status_code, r.json())
"
```

Real policy types (this build's own hardcoded A1TD catalog sample):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://a1-related:8000/policy-types')
print(r.status_code, r.json())
"
```

Create an A1 Policy — a real round trip to the mock Near-RT RIC
(`A1TerminationClient.create_policy`), not a local stub:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://a1-related:8000/policies', json={
    'policyTypeId': 'ORAN_QoSandTSP_6.0.1',
    'policyObject': {'scope': {'cellId': 'demo-cell-1'}, 'qosObjectives': {'gfbr': 100}},
    'nearRtRicId': 'mock-near-rt-ric-001', 'creatorId': 'hello-world-rapp',
})
print(r.status_code, r.json())
"
```

`enforcementStatus` is `ENFORCED` — the mock Near-RT RIC genuinely
accepted it. Subscribe to status changes on it:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://a1-related:8000/policies/subscriptions', json={
    'notificationDestination': 'http://demo-consumer:9000/policy-status',
    'policyIdList': ['<policyId>'],
})
print(r.status_code, r.json())
"
```

**A real duplicate-policy rejection** — create a second policy with the
exact same type and content as the first; the mock Near-RT RIC's own
content-fingerprint check (adopted from the real near-rt-ric-simulator's
`calcFingerprint`) rejects it, not a scripted failure:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://a1-related:8000/policies', json={
    'policyTypeId': 'ORAN_QoSandTSP_6.0.1',
    'policyObject': {'scope': {'cellId': 'demo-cell-1'}, 'qosObjectives': {'gfbr': 100}},
    'nearRtRicId': 'mock-near-rt-ric-001', 'creatorId': 'hello-world-rapp',
})
print(r.status_code, r.json())
"
```

`enforcementStatus` is `REJECTED` — A1 Related still stores the mapping
(so it's queryable), but the RIC-side content collision is real, not
simulated locally by A1 Related itself.

Now update the first policy to an empty object — the mock's own
`REJECTED`-on-empty rule fires a genuine `ENFORCED -> REJECTED`
transition, which is exactly what the subscription above exists to
observe:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.put('http://a1-related:8000/policies/<policyId>', json={})
print(r.status_code, r.json())
"
```

`enforcementStatus` is now `REJECTED`, and because it genuinely changed
from `ENFORCED`, `_notify_policy_status_subscribers` fired a real POST
to `http://demo-consumer:9000/policy-status` — no real listener exists
at that address in this compose stack (same honesty pattern as FOCOM's
and Policy Mgmt's placeholder callbacks above), so watch `a1-related`'s
own logs for the delivery attempt;
`tests_integration/test_demo_runbook.py` proves the real dispatch fires
by intercepting the exact `httpx.post` call.

Retract everything — delete both policies, then deregister the service
(which would itself cascade-delete any policies still attached to it):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://a1-related:8000/policies/<policyId>')
print(r.status_code)
r2 = httpx.delete('http://a1-related:8000/policies/<duplicatePolicyId>')
print(r2.status_code)
r3 = httpx.delete('http://a1-related:8000/services/hello-world-rapp')
print(r3.status_code)
"
```

## 11. Retire it — Terminate, then Delete

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
— onboard, deploy, bootstrap, register, operate, RAN NF OAM closed
loop, FOCOM resource management, Policy Mgmt intent automation, A1
Policy Management, retire — is now complete against a real running
stack.

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
