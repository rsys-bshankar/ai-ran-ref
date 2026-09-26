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

**A real validation failure** — `_validate_package`'s own duplicate-
content check (adapted from the reference's `AsdDescriptorValidator`
descriptor-id uniqueness rule to this build's own package identity, a
content hash) rejects a byte-identical package that's already
onboarded. Onboard the exact same CSAR a second time:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://onboarding:8000/packages', json={
    'location': 'http://r1-termination:8899/hello-world-rapp.csar',
})
print(r.status_code, r.json())
"
```

This still returns `202` — `OnboardPackage`'s own async contract never
rejects synchronously, success or failure is only ever observable via
`onboarding-status`, matching every other outcome in this endpoint.
Poll the new `packageId`:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://onboarding:8000/packages/<secondPackageId>/onboarding-status')
print(r.json())
"
```

`state` is `FAILED` — a real, deliberate rejection (the same
`integrity_hash` already exists on the first package), not a bug.
Nothing about this second package (name, version, artifacts,
`nfDeploymentDescriptorId`) was ever populated — the FSM's own
`VALIDATE_FAILED` transition fires before any of that work happens.
The first package (from the step above) is untouched and still
`AVAILABLE` — this failure path is fully independent of the one this
runbook actually deploys.

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

**FOCOM FCAPS** — a distinct domain from RAN NF OAM's RAN-function
alarms (NFO+FOCOM LLD section 1): infrastructure/O-Cloud host alarms
and performance. Ingest a real infrastructure alarm against the Phase 1
degenerate cluster:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://focom:8000/alarms/ingest', params={'resource_ref': 'phase1-degenerate-cluster', 'severity': 'critical'})
print(r.status_code, r.json())
"
```

Confirm it's queryable:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://focom:8000/alarms')
print(r.status_code, r.json())
"
```

Performance metrics are also genuinely queryable, filterable by
`resource_ref`:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://focom:8000/performance')
print(r.status_code, r.json())
"
```

This returns `[]` in a fresh stack — honestly, not a bug: there is no
`POST /performance` route in this build at all, matching the same
"no real southbound collection pipeline" elision already documented for
RAN NF OAM's PM subscriptions — real O-Cloud performance metrics would
arrive via O2ims's own collection mechanism, not an rApp-facing write.
The route itself, and its filter, are real and already unit-tested
(`test_performance_metrics_filterable_by_resource`); nothing here is
stubbed, there is simply nothing to collect from in a docker-run-based
Phase 1.

## 10. Policy Mgmt intent automation (optional) — register, dispatch, retract

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

**A real negative case** — `intentHandlingScope` is a genuine pre-filter
(`_matching_rmihs`), not decoration: register a second RMIH with the
*same* declared capability (`RAN_SUBNETWORK`) but a *different*
declared scope (`CN`-only):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://policy-mgmt:8000/intent-handling-functions', json={
    'rmihId': 'sa-smos', 'smeServiceId': 'sa-smos-svc',
    'capabilities': [{'supportedExpectationObjectType': 'RAN_SUBNETWORK'}],
    'notificationCallbackUri': 'http://sa-smos:8000/intents/notify',
    'intentHandlingScope': ['CN'],
})
print(r.status_code, r.json())
"
```

Create a second, `RAN`-scoped Intent with the identical
`RAN_SUBNETWORK` expectation object type both RMIHs declare support
for:

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

Only `so-smos` (declared `RAN` scope) is dispatched a notification —
`sa-smos`'s matching *capability* is correctly never enough on its own,
because its declared `CN`-only scope fails the pre-filter before the
capability check ever runs. Watch `policy-mgmt`'s own logs: exactly one
delivery attempt, to `so-smos:8000/intents/notify`, never to
`sa-smos:8000/intents/notify`. `tests_integration/test_demo_runbook.py`
asserts this precisely — one notification, not two, and to the right
RMIH.

Retract both Intents, then deregister both RMIHs — symmetric teardown,
same pattern as FOCOM's provision/deprovision above:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://policy-mgmt:8000/intents/<intentId>')
print(r.status_code)
r2 = httpx.delete('http://policy-mgmt:8000/intents/<secondIntentId>')
print(r2.status_code)
r3 = httpx.delete('http://policy-mgmt:8000/intent-handling-functions/so-smos')
print(r3.status_code)
r4 = httpx.delete('http://policy-mgmt:8000/intent-handling-functions/sa-smos')
print(r4.status_code)
"
```

## 11. A1 Policy Management (optional) — register, enforce, a real duplicate rejection, retract

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

## 12. SME Trusted Invokers (optional) — register, query, revoke a real security context

Independent of the sample rApp instance above — this exercises the real
CAPIF core's second, separate security mechanism beyond OAuth2 token
issuance (`capifcore/internal/securityservice/security.go`): a per-AEF
security context a real AEF (resource server) would consult directly,
not something the token endpoint itself ever reads. Previously entirely
absent from this build (SPEC_AUDIT.md SME item 2).

Reuse the `apiInvokerId` from step 4's invoker registration. Register a
security context for it — this genuinely requires the invoker already
be onboarded:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.put('http://sme:8000/trusted-invokers/<apiInvokerId>', json={
    'notificationDestination': 'http://demo-consumer:9000/security-notify',
    'securityInfo': [{'aefId': 'hello-world-rapp', 'apiId': 'helloworld-api', 'authenticationInfo': 'demo-auth-info',
                       'authorizationInfo': 'demo-authz-info', 'prefSecurityMethods': ['OAUTH']}],
})
print(r.status_code, r.json())
"
```

`201` — `selSecurityMethod` is the invoker's own first preferred method
(this build has no real per-AEF security-method catalog to cross-check
against, honestly, the same "unknown real content, permissive
placeholder" pattern used elsewhere). Query it back — by default,
`authenticationInfo`/`authorizationInfo` are redacted:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://sme:8000/trusted-invokers/<apiInvokerId>')
print(r.status_code, r.json())
"
```

Both fields come back as empty strings. Ask for them explicitly:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://sme:8000/trusted-invokers/<apiInvokerId>', params={'authentication_info': True, 'authorization_info': True})
print(r.status_code, r.json())
"
```

Now the real values come back. **Revoke** the context for this one
AEF — a real, partial removal, not a full delete:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/trusted-invokers/<apiInvokerId>/delete', json={
    'aefId': 'hello-world-rapp', 'apiIds': ['helloworld-api'], 'apiInvokerId': '<apiInvokerId>', 'cause': 'UNEXPECTED_REASON',
})
print(r.status_code)
"
```

Since that was the only `securityInfo` entry, the whole trusted-invoker
record is now gone — confirm with a 404:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://sme:8000/trusted-invokers/<apiInvokerId>')
print(r.status_code, r.json())
"
```

## 13. AI/ML Workflow (optional) — register, train, upload/download a real artifact, advance to ACTIVE

Independent of the sample rApp instance above — a whole module never
touched by this runbook before. Real MLModel lifecycle FSM (SMO Design
v1.3 section 3.8), a real training-job round trip, and real artifact
bytes that genuinely round-trip through Postgres, not a stub.

Register a model with real metadata:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ai-ml-workflow:8000/models', json={
    'modelType': 'hello-world-anomaly-detector', 'version': '1.0.0',
    'description': 'Demo anomaly-detection model for the hello-world rApp',
    'author': 'hello-world-rapp', 'owner': 'hello-world-rapp',
    'inputDataType': 'application/json', 'outputDataType': 'application/json',
})
print(r.status_code, r.json())
"
```

Note the `modelId` — `state` is `REGISTERED`. Request training against
it — a real FSM transition (`REGISTERED -> TRAINING`, `TRAIN`):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ai-ml-workflow:8000/training-jobs', json={
    'modelId': '<modelId>', 'producerId': 'hello-world-rapp',
    'runId': 'demo-run-1', 'trainingDataset': 's3://demo/hello-world-train',
    'validationDataset': 's3://demo/hello-world-val',
})
print(r.status_code, r.json())
"
```

Note the `trainingJobId`. Confirm the model really moved to `TRAINING`:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://ai-ml-workflow:8000/models/<modelId>')
print(r.status_code, r.json())
"
```

**Upload a real model artifact** — the bytes genuinely round-trip
through a Postgres-backed `ModelArtifact` row, not a discarded stub
(real S3 storage is the one deliberate elision here):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ai-ml-workflow:8000/models/<modelId>/artifact',
                files={'file': ('hello-world-model.zip', b'demo-model-weights-bytes', 'application/zip')})
print(r.status_code, r.json())
"
```

Note `artifactVersion` (1). Write real training metrics, matching the
reference's own whole-body-replace semantics:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ai-ml-workflow:8000/training-jobs/<trainingJobId>/model-metrics', json={'accuracy': 0.94, 'f1Score': 0.91})
print(r.status_code, r.json())
"
```

**Advance the model through its real lifecycle FSM** — each step is a
genuine state transition, not a fast-forward:

```bash
docker compose exec r1-termination python3 -c "
import httpx
for event in ['TRAINING_COMPLETE', 'VALIDATION_COMPLETE', 'CERTIFY', 'LOAD', 'ACTIVATE']:
    r = httpx.post('http://ai-ml-workflow:8000/models/<modelId>/advance', params={'event': event})
    print(event, '->', r.status_code, r.json()['state'])
"
```

Ending state is `ACTIVE`. Download the artifact back and confirm the
bytes really match what was uploaded:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://ai-ml-workflow:8000/models/<modelId>/artifact/1')
print(r.status_code, r.content == b'demo-model-weights-bytes')
"
```

Deregister — real cascade cleanup of the artifact and training-job rows:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://ai-ml-workflow:8000/models/<modelId>')
print(r.status_code)
"
```

## 14. RAN Analytics (optional) — register a producer, subscribe, publish a real report

Independent of the sample rApp instance above — the last of the four
modules never touched by any demo phase before this pass. Real
producer registration (which itself does the same real two-step CAPIF
dance as step 4: SME provider enrolment then service publish), a real
subscription, and a real report-publish that genuinely notifies its
subscriber.

Register an analytics producer:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ran-analytics:8000/producers',
                params={'producer_id': 'hello-world-rapp', 'analytics_type': 'coverage-issue-analysis'},
                json={'dme_input_types': [], 'output_schema': {'type': 'object', 'properties': {'issue': {'type': 'string'}}}})
print(r.status_code, r.json())
"
```

`hello-world-rapp` was already SME-enrolled in step 4 — this
re-registers the same provider (idempotent) and publishes a second,
distinct service (`mdaf.coverage-issue-analysis`) for it, the same
real cross-module wiring `register_analytics_producer` always does.
Confirm it's a real, queryable registration:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://ran-analytics:8000/producers', params={'analytics_type': 'coverage-issue-analysis'})
print(r.status_code, r.json())
"
```

Subscribe, with a real notification destination:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ran-analytics:8000/subscriptions', params={
    'analytics_type': 'coverage-issue-analysis', 'requested_by': 'sa-smos',
    'notification_destination': 'http://demo-consumer:9000/analytics-reports',
})
print(r.status_code, r.json())
"
```

Note the `subscriptionId`, then publish a report:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ran-analytics:8000/reports', params={'analytics_type': 'coverage-issue-analysis'},
                json={'output': {'issue': 'demo-cell-1 coverage hole detected'}, 'input_sources': []})
print(r.status_code, r.json())
"
```

`publish_report` fired a real notification to
`http://demo-consumer:9000/analytics-reports` — no real listener
exists at that address in this compose stack (same honesty pattern as
FOCOM's/Policy Mgmt's/A1 Related's placeholder callbacks above), so
watch `ran-analytics`'s own logs for the delivery attempt;
`tests_integration/test_demo_runbook.py` proves the real dispatch
fires with the correct `reportId`/`output` payload, by intercepting
the exact `httpx.post` call `_notify_report_subscribers` makes.
Confirm the report is queryable:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://ran-analytics:8000/reports', params={'analytics_type': 'coverage-issue-analysis'})
print(r.status_code, r.json())
"
```

Unsubscribe:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://ran-analytics:8000/subscriptions/<subscriptionId>')
print(r.status_code)
"
```

## 15. SA SMOS (optional) — a real assurance monitor, a genuine `RECONNECT` heal, and a genuine `ROLLBACK` refusal

Independent of the sample rApp instance above — the last of the
six-item follow-up sequence. SA SMOS's own real remedial-action
dispatch (SO/SA SMOS LLD section 2.1) was already implemented and
unit-tested, but no demo phase had ever exercised it. `RECONNECT`
needs a genuine, `RUNNING` `NFDeployment` to reconnect — the sample
rApp's own deployment (step 3) can't be reused, since NFO's real
duplication guard means a `NFDeploymentDescriptor` may only be
deployed once — so this creates a second, independent deployment of
the same already-onboarded package first, via SO SMOS's own real
dispatch table (the next section exercises it further).

Create a second `NFDeploymentDescriptor` against the package onboarded
in step 2 (`CreateDescriptor` has no per-package uniqueness
constraint — only *deploying* the same descriptor twice is rejected):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://nfo:8000/descriptors', json={
    'packageId': '<packageId>', 'name': 'sa-smos-demo-descriptor',
})
print(r.status_code, r.json())
"
```

Note the new `nfDeploymentDescriptorId`, then submit a real SO SMOS
order with a single `DEPLOY` step targeting it — the same dispatch
table the next section exercises further, this time reaching NFO's
real `Instantiate`:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://so-smos:8000/orders', json={
    'scope': 'sa-smos-demo-deploy',
    'steps': [
        {'stepType': 'DEPLOY', 'targetModule': 'NFO', 'nfDeploymentDescriptorId': '<newDescriptorId>', 'name': 'sa-smos-demo-deployment'},
    ],
})
print(r.status_code, r.json())
"
```

The single step is `COMPLETED`; its `result` carries a real
`nfDeploymentId` in state `RUNNING` (NFO's own `INSTANTIATE_COMPLETE`
transition, the same as the sample rApp's own deployment). Note the
`orderId`.

Register an `AssuranceMonitor` scoped to that order:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sa-smos:8000/monitors', params={'target_order_id': '<orderId>'}, json={'latency': 100})
print(r.status_code, r.json())
"
```

Note the `monitorId`. Evaluate it against a real metrics sample that
breaches the threshold:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sa-smos:8000/monitors/<monitorId>/evaluate', json={'latency': 80})
print(r.status_code, r.json())
"
```

`breaches` shows `{'latency': 100}` — a real threshold comparison, not
a stub.

**A genuine `RECONNECT`** — `ExecuteRemedialAction` resolves the
monitor's `target_order_id` back into a concrete `nfDeploymentId` by
reading SO SMOS's own order record (`_resolve_deployed_nf`, finding
the `DEPLOY` step's `COMPLETED` result) and dispatches NFO's real
`Heal`:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sa-smos:8000/monitors/<monitorId>/remedial-actions', params={'action_type': 'RECONNECT'})
print(r.status_code, r.json())
"
```

`outcome` is `RESOLVED` — NFO's `Heal` route genuinely fired (from
`RUNNING`, idempotent, matching the reference's absence of a real
"unhealthy" concept — see `nfo/app/main.py`'s own `heal()` docstring)
and recorded a real `LCMOperation` row.

**A genuine `ROLLBACK` refusal** — this is not a generic "ambiguous
meaning" stub. rApp Management's own `UpgradeInstance` deletes the
previous `RAppInstance` row on a successful commit, so no
package-version history survives to roll back to at all:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sa-smos:8000/monitors/<monitorId>/remedial-actions', params={'action_type': 'ROLLBACK'})
print(r.status_code, r.json())
"
```

`501`, with `detail.title` = `ROLLBACK_HISTORY_UNAVAILABLE` and a
concrete explanation, not a generic error.

Retire the second deployment — NFO's real `Terminate` (from `RUNNING`,
this build's Phase 1 elision completes the delete synchronously):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://nfo:8000/deployments/<nfDeploymentId>')
print(r.status_code)
"
```

## 16. SO SMOS (optional) — a real multi-step order, fail-fast, cancel

Independent of the sample rApp instance above — this exercises SO
SMOS's own real dispatch table (SO/SA SMOS LLD section 1): a single
order's steps are dispatched in sequence to whichever downstream
module each `stepType`/`targetModule` pair maps to, over the real R1
client — not a placeholder. Section 1.1's own design decision is
**fail-fast**: the first failed step halts the order; every step after
it stays `PENDING`, never attempted; completed steps are not
auto-rolled-back (no compensating-transaction mechanism exists in
Phase 1).

Submit a 3-step order — a real `FOCOM` provision, a `POLICY` step
against a policy type A1 Related doesn't recognize (a genuine
downstream rejection, not a scripted one), and a `TRAINING` step that
should never actually be attempted:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://so-smos:8000/orders', json={
    'scope': 'demo-multi-step-order',
    'steps': [
        {'stepType': 'INFRA', 'targetModule': 'FOCOM', 'spec': {'resourceTypeId': 'gpu-l40', 'description': 'SO SMOS provisioned node'}},
        {'stepType': 'POLICY', 'targetModule': 'A1_RELATED', 'policyTypeId': 'NOT_A_REAL_POLICY_TYPE',
         'policyObject': {'scope': {'cellId': 'demo-cell-1'}}, 'nearRtRicId': 'mock-near-rt-ric-001'},
        {'stepType': 'TRAINING', 'targetModule': 'AI_ML_WORKFLOW', 'producerId': 'hello-world-rapp'},
    ],
})
print(r.status_code, r.json())
"
```

The response shows all three steps' real outcomes in one call: step 1
`COMPLETED` (a genuine new `Resource` row now exists in FOCOM), step 2
`FAILED` (A1 Related's own real `POLICY_TYPE_NOT_SUPPORTED` rejection,
surfaced as `DownstreamError` — SO SMOS's own dispatch layer
distinguishes this from a transport failure, per its own docstring on
a real bug this caught: a downstream error response was previously
recorded as `COMPLETED` with the error body as the "result"), and step
3 `PENDING` — the order halted before `AI_ML_WORKFLOW` was ever
dispatched to. Note the `orderId`, then confirm the persisted state:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://so-smos:8000/orders/<orderId>')
print(r.status_code, r.json())
"
```

**Cancel** the order — the real, genuinely-tested fix (this route used
to silently never persist the cancellation at all, since mutating a
plain JSON column's list in place is invisible to SQLAlchemy's change
tracking) turns the still-`PENDING` step `CANCELLED`, leaving the
already-`COMPLETED`/`FAILED` steps untouched:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://so-smos:8000/orders/<orderId>/cancel')
print(r.status_code, r.json())
"
```

## 17. DME type subscriptions (optional) — notify a consumer when a type is registered or removed

Independent of the sample rApp instance above — DME's own real
type-subscription mechanism (ICS's own `/info-type-subscription`,
`InfoTypeSubscriptions`/`ConsumerCallbacks`), closed in an earlier
pass but never demonstrated: a consumer notified whenever *any*
`DmeType` is registered or removed, unfiltered (matching the
reference's own lack of per-type scoping).

Subscribe first, with a real notification destination:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://dme:8000/type-subscriptions', json={
    'notificationDestination': 'http://demo-consumer:9000/dme-type-events', 'owner': 'hello-world-rapp',
})
print(r.status_code, r.json())
"
```

Note the `subscriptionId`. Register a new DME type — `register_dme_type`
fires a real `REGISTERED` notification to every subscriber
(`_notify_type_subscribers`):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://dme:8000/production-capabilities', json={
    'namespace': 'demo', 'name': 'dme-type-sub-demo', 'version': '1.0',
    'typeName': 'dme-type-sub-demo-v1', 'producerId': 'hello-world-rapp',
    'dataProductionSchema': {'type': 'object', 'properties': {'reading': {'type': 'number'}}},
    'producerHealthCallbackUrl': 'http://hello-world-rapp:8080/health',
    'jobCallbackUrl': 'http://hello-world-rapp:8080/dme-jobs',
})
print(r.status_code, r.json())
"
```

No real listener exists at `http://demo-consumer:9000/dme-type-events`
in this compose stack (same honesty pattern as every other placeholder
callback in this runbook), so watch `dme`'s own logs for the attempted
delivery — a real POST with `{infoTypeId, jobDataSchema, status:
"REGISTERED"}`. `tests_integration/test_demo_runbook.py` proves the
real dispatch fires with the correct payload by intercepting the exact
`httpx.post` call.

Deregister the producer — `deregister_producer` fires a matching
`DEREGISTERED` notification for the same type the same way:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://dme:8000/production-capabilities', params={'producer_id': 'hello-world-rapp'})
print(r.status_code)
"
```

Note this also deregisters `hello-world-rapp`'s own `hello-world-metrics`
type from step 4, alongside the new demo one — `deregister_producer`
tears down every `DmeType` a `producer_id` owns, matching ICS's own
scope. Unsubscribe:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://dme:8000/type-subscriptions/<subscriptionId>')
print(r.status_code)
"
```

## 18. FOCOM topology export (optional) — TEIV entities and relationships from real inventory rows

Independent of the sample rApp instance above — closed in an earlier
§5 pass (the Blueprint names "FOCOM's placement as a TEIV data source"
as a confirmed integration point) but never demonstrated. A real
kubeconfig-driven `focom-to-teiv-adapter` pushing CloudEvents over
Kafka is structurally out of scope for this docker-run Phase 1 (no
message broker anywhere in this build); `GET /topology` is the honest
pull-based substitute — the same real `ResourceType`/`ResourcePool`/
`DeploymentManager`/`Resource` rows every other FOCOM drill-down route
already reads, exported in the reference's own wire shape
(`o-ran-smo-teiv-cloud:<EntityType>` keys, `{id, attributes}` for
entities, `{id, aSide, bSide, sourceIds}` for relationships).

By this point in the runbook, FOCOM already has real inventory beyond
the seeded Phase 1 topology — step 15's `gpu-l40` `Resource` from SO
SMOS's own `INFRA` step (never deprovisioned there):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://focom:8000/topology')
print(r.status_code, r.json())
"
```

`entities` includes a real `ResourceType` for both `generic` (Phase 1's
own seeded type) and `gpu-l40` (auto-registered the moment step 15
provisioned against it — `provision_resource`'s own real behavior, not
this endpoint's), a `ResourcePool`, a `DeploymentManager`, and at least
one real `Resource`. `relationships` are built only from this schema's
real foreign keys — `RESOURCE_IS_OF_TYPE_RESOURCETYPE` and
`RESOURCE_CONTAINED_IN_RESOURCEPOOL` for every `Resource` row, plus a
`RESOURCE_CHILD_OF_RESOURCE` entry for any with a real `parentId` (none
in this Phase 1 topology, so that key is genuinely absent rather than
an empty placeholder) — never invented ones.

## 19. AI/ML Workflow feature groups (optional) — register, list, a real duplicate-name rejection

Independent of the sample rApp instance above — a whole entity added
in an earlier §5 pass (the reference's own `CreateFeatureGroup`,
`featuregroup_controller.py`) but never touched by any demo phase.
Real Cassandra-backed feature-store queries and `enableDme`'s real DME
job creation are deliberate elisions (the same no-real-southbound-
compute pattern as the rest of this module) — this exercises the real
part: registration, listing, and the reference's own name-validation
and duplicate-name rejection.

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ai-ml-workflow:8000/feature-groups', json={
    'featureGroupName': 'demo_coverage_features', 'featureList': 'rsrp,rsrq,sinr',
    'datalakeSource': 'INFLUX', 'host': 'influx.demo', 'port': '8086', 'bucket': 'demo-bucket',
    'token': 'demo-token', 'dbOrg': 'demo-org', 'measurement': 'coverage_metrics',
})
print(r.status_code, r.json())
"
```

Note the `featureGroupId`. Confirm it's a real, queryable registration:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://ai-ml-workflow:8000/feature-groups')
print(r.status_code, r.json())
"
```

**A real duplicate-name rejection** — register the exact same
`featureGroupName` again; the reference's own `DBException` ("already
exist") fires for real, via a genuine `UniqueConstraint`, not a
scripted check:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ai-ml-workflow:8000/feature-groups', json={
    'featureGroupName': 'demo_coverage_features', 'featureList': 'rsrp,rsrq,sinr',
    'datalakeSource': 'INFLUX', 'host': 'influx.demo', 'port': '8086', 'bucket': 'demo-bucket',
    'token': 'demo-token', 'dbOrg': 'demo-org', 'measurement': 'coverage_metrics',
})
print(r.status_code, r.json())
"
```

`409`, `detail.title` = `FEATURE_GROUP_ALREADY_REGISTERED`. Also a real
rejection for an invalid name (the reference's own `\w+`, 3-63
character rule, shared with `TrainingJob` names):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ai-ml-workflow:8000/feature-groups', json={
    'featureGroupName': 'no spaces allowed', 'featureList': 'rsrp', 'datalakeSource': 'INFLUX',
    'host': 'influx.demo', 'port': '8086', 'bucket': 'demo-bucket', 'token': 'demo-token',
    'dbOrg': 'demo-org', 'measurement': 'coverage_metrics',
})
print(r.status_code, r.json())
"
```

`400`, `detail.title` = `FEATURE_GROUP_NAME_INVALID`.

## 20. SA SMOS coordination-group remedial action (optional) — a real group retrain, `actionType`-independent

Independent of the sample rApp instance above — SA SMOS's own
`MLModelCoordinationGroup` convergence (OPEN_ITEMS.md section 1): a
coordination-group-scoped `AssuranceMonitor` bypasses
`CONFIG_CHANGE`/`SCALE`/`RECONNECT`/`ROLLBACK`'s NF-deployment meanings
entirely — those don't map onto a model group at all — and always
dispatches a real group retrain via AI/ML Workflow's `RequestTraining`
instead, whatever `actionType` was requested. Already real and
unit-tested, but never demonstrated: step 15's own `AssuranceMonitor`
was `targetOrderId`-scoped throughout.

Register two models to be the group's members — a coordination group of
fewer than two members isn't a coordination of anything, and the
migration's own `member_model_ids` CHECK constraint (`array_length >= 2`)
enforces this at the DB layer:

```bash
docker compose exec r1-termination python3 -c "
import httpx
for i in range(2):
    r = httpx.post('http://ai-ml-workflow:8000/models', json={
        'modelType': f'demo-coordination-group-model-{i}', 'version': '1.0.0',
        'author': 'hello-world-rapp', 'owner': 'hello-world-rapp',
    })
    print(r.status_code, r.json())
"
```

Note both `modelId`s, then create a real coordination group with them:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://ai-ml-workflow:8000/coordination-groups', json={'memberModelIds': ['<modelId1>', '<modelId2>']})
print(r.status_code, r.json())
"
```

A single-member `memberModelIds` 422s with `COORDINATION_GROUP_TOO_SMALL`
— pre-validated here after this pass found the route 500ing against real
Postgres instead (the DB's own CHECK constraint, never mirrored onto the
ORM model, so no unit test running against SQLite had ever caught it).

Note the `groupId`, then register an `AssuranceMonitor` scoped to it —
`targetCoordinationGroupId`, not `targetOrderId`:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sa-smos:8000/monitors', params={'target_coordination_group_id': '<groupId>'}, json={})
print(r.status_code, r.json())
"
```

**Execute a remedial action** — `SCALE` on its own would always
`ESCALATED` for an order-scoped monitor (NFO's own Phase 1 stub), but
this monitor is group-scoped, so `execute_remedial_action` never even
reaches that branch:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sa-smos:8000/monitors/<monitorId>/remedial-actions', params={'action_type': 'SCALE'})
print(r.status_code, r.json())
"
```

`outcome` is `RESOLVED` — SA SMOS dispatched a real
`POST /ai-ml-workflow/training-jobs` with the group's own
`modelCoordinationGroupId`, converging with AI/ML Workflow's own
`groupRetrainTriggered` mechanism. Confirm the real `TrainingJob` row
this created:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://ai-ml-workflow:8000/training-jobs', params={'status': 'RUNNING'})
print(r.status_code, [j for j in r.json() if j['modelCoordinationGroupId'] == '<groupId>'])
"
```

A real `TrainingJob` with `producerId: sa-smos` and this exact
`modelCoordinationGroupId` — not a fabricated confirmation.

## 21. SME event-subscription `apiId` filtering (optional) — a subscriber scoped to one service, not every service

Independent of the sample rApp instance above — SME's own
`SubscribeEvents` mechanism (the reference's `CAPIFEventFilter`,
`eventservice.go`'s `getMatchingSubs`) has always filtered by
`eventTypes`, real and unit-tested since an earlier pass, but never
demonstrated at all: no `capif-events` subscription has appeared
anywhere in this runbook until now. Of the reference's other filter
dimensions (`apiId`/`apiInvokerId`/`aefId`), only `apiId` is
meaningfully implementable here — it maps directly onto this build's
own `serviceId`.

Subscribe two consumers: `consumer-unscoped` gets every
`SERVICE_API_UPDATE`, `consumer-scoped` only wants `helloworld-api`'s
own (its `serviceId`, from step 4's own registration response):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/capif-events/v1/consumer-unscoped/subscriptions', json={
    'subscriberId': 'consumer-unscoped', 'eventTypes': ['SERVICE_API_UPDATE'],
    'callbackUri': 'http://demo-consumer:9000/sme-events-unscoped',
})
print(r.status_code, r.json())
r = httpx.post('http://sme:8000/capif-events/v1/consumer-scoped/subscriptions', json={
    'subscriberId': 'consumer-scoped', 'eventTypes': ['SERVICE_API_UPDATE'],
    'callbackUri': 'http://demo-consumer:9000/sme-events-scoped', 'apiIds': ['<helloworldServiceId>'],
})
print(r.status_code, r.json())
"
```

Register an unrelated second service, then re-register it (`UPDATE`) —
this fires, but only reaches `consumer-unscoped`; `consumer-scoped`'s
own `apiIds` filter excludes it (`notify_service_change`'s
`sub.api_ids and str(service.service_id) not in sub.api_ids` check):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/published-apis/v1/hello-world-rapp/service-apis', json={
    'serviceName': 'other-api', 'producerId': 'hello-world-rapp',
    'endpoint': 'http://hello-world-rapp:8080/other/v1', 'version': '1.0', 'moduleScope': 'hello-world-rapp',
})
print(r.status_code, r.json())
r = httpx.post('http://sme:8000/published-apis/v1/hello-world-rapp/service-apis', json={
    'serviceName': 'other-api', 'producerId': 'hello-world-rapp',
    'endpoint': 'http://hello-world-rapp:8080/other/v1', 'version': '2.0', 'moduleScope': 'hello-world-rapp',
})
print(r.status_code, r.json())
"
```

No real listener exists at `http://demo-consumer:9000/...` in this
compose stack (same honesty pattern as every other placeholder callback
in this runbook), so watch `sme`'s own logs — exactly one attempted
delivery, to `consumer-unscoped`'s callback only.
`tests_integration/test_demo_runbook.py` proves this precisely by
intercepting the exact `httpx.post` calls.

Now re-register `helloworld-api` itself (`UPDATE`) — this one matches
`consumer-scoped`'s own `apiIds` filter too, so **both** subscribers
are notified:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://sme:8000/published-apis/v1/hello-world-rapp/service-apis', json={
    'serviceName': 'helloworld-api', 'producerId': 'hello-world-rapp',
    'endpoint': 'http://hello-world-rapp:8080/helloworld/v1', 'version': 'v2',
    'fullApiVersions': ['v1'], 'moduleScope': 'hello-world-rapp',
})
print(r.status_code, r.json())
"
```

Watch `sme`'s own logs again — two attempted deliveries this time, one
to each callback. Unsubscribe both:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://sme:8000/capif-events/v1/consumer-unscoped/subscriptions/<unscopedSubscriptionId>')
print(r.status_code)
r = httpx.delete('http://sme:8000/capif-events/v1/consumer-scoped/subscriptions/<scopedSubscriptionId>')
print(r.status_code)
"
```

## 22. A1 Related's service supervision sweep (optional) — a real, non-zero `keepAliveIntervalSeconds`

Independent of the sample rApp instance above — step 11's own
`putService` call used `keepAliveIntervalSeconds: 0` (supervision
disabled) throughout, so the reference's own supervision contract
("When a service fails to invoke keepalive within the configured time,
the service is considered unavailable... automatically deregistered and
its policies will be deleted") has been real and unit-tested since an
earlier §5 pass, but has never actually fired in this runbook. No
scheduler exists anywhere in this build — the sweep happens lazily, on
the next `GET /services` read (`_sweep_stale_service`), not on a timer.

Register a new supervised service with a real, short interval, and
create a policy under it:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.put('http://a1-related:8000/services', json={'serviceId': 'demo-supervised-rapp', 'keepAliveIntervalSeconds': 2})
print(r.status_code, r.json())
r = httpx.post('http://a1-related:8000/policies', json={
    'policyTypeId': 'ORAN_QoSandTSP_6.0.1',
    'policyObject': {'scope': {'cellId': 'demo-cell-2'}, 'qosObjectives': {'gfbr': 50}},
    'nearRtRicId': 'mock-near-rt-ric-001', 'creatorId': 'demo-supervised-rapp',
})
print(r.status_code, r.json())
"
```

Now let the 2-second interval elapse **without** calling
`PUT /services/demo-supervised-rapp/keepalive` — a real service that
stopped heartbeating:

```bash
sleep 3
```

`GET /services` is the read path that actually enforces supervision — a
stale match is swept on its way out, not just reported as stale:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://a1-related:8000/services', params={'service_id': 'demo-supervised-rapp'})
print(r.status_code, r.json() if r.status_code == 200 else None)
"
```

`404` — genuinely deregistered, not just still-listed-as-stale. Confirm
its policy was torn down the same way an explicit retract does it (a
real southbound `a1t.delete_policy` call per policy, not just a local
row delete):

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.get('http://a1-related:8000/policies', params={'creator_id': 'demo-supervised-rapp'})
print(r.status_code, r.json())
"
```

`[]` — the policy created above is gone, swept alongside its own
service, exactly as `keepAliveIntervalSeconds`'s own contract promises.

## 23. Retire it — package priming lifecycle, Terminate, then Delete

**Prime the package** — the reference's real
`COMMISSIONED -> PRIMING -> PRIMED` lifecycle (our `AVAILABLE` plays
the `COMMISSIONED` role), previously entirely absent from this
runbook. Real ACM/DME/SME resource pre-provisioning behind it is out
of scope, so both transitions fire within this one request:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://onboarding:8000/packages/<packageId>/prime')
print(r.status_code, r.json())
"
```

`state` is now `PRIMED`.

**A real deprime refusal** — attempt to deprime it while the sample
rApp's own instance (from step 3) is still deployed against it. This
is the reference's own `deprimeRapp` guard ("Unable to deprime as
there are active rapp instances"), backed by a real query against
`PackageUsageRegistration`, not a scripted failure:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://onboarding:8000/packages/<packageId>/deprime')
print(r.status_code, r.json())
"
```

`409`, with `detail.title` = `SERVICE_NAME_CONFLICT` — the package
stays `PRIMED`.

Now terminate the instance:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://rapp-mgmt:8000/instances/<instanceId>/terminate')
print(r.status_code, r.json())
"
```

`state` should be `UNDEPLOYED`. `TerminateInstance` also calls
Onboarding's real `usage/stop` internally (rApp Mgmt LLD), closing the
active usage registration the deprime guard above was reading.

**Deprime again** — now genuinely succeeds, the same guard this time
passing for real, not just a state flag flipped by hand:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.post('http://onboarding:8000/packages/<packageId>/deprime')
print(r.status_code, r.json())
"
```

`state` is back to `AVAILABLE`. Then the real, separate delete of the
instance:

```bash
docker compose exec r1-termination python3 -c "
import httpx
r = httpx.delete('http://rapp-mgmt:8000/instances/<instanceId>')
print(r.status_code)
"
```

204 with an empty body — the instance row is gone. The full lifecycle
— onboard, deploy, bootstrap, register, operate, RAN NF OAM closed
loop, FOCOM resource management, FOCOM FCAPS, Policy Mgmt intent
automation, A1 Policy Management, SME Trusted Invokers, AI/ML Workflow,
RAN Analytics, SO SMOS, SA SMOS, package priming, retire — is now
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
