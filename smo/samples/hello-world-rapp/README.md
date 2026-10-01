# Hello World rApp

A minimal, non-AI sample package that drives the full rApp lifecycle (onboard, deploy, activate, operate, retire) against the platform. It has no code of its own: it is a CSAR of descriptors, one Helm chart and reference registration bodies.

## At a glance

| | |
|---|---|
| Use case | Lifecycle demonstration, no AI |
| Model | None |
| O1 targets / actuators | None |
| Datasets | Produces and consumes its own `hello-world-metrics` DME type (`demo` namespace, version 1.0) |
| Autonomy modes | None declared |
| R1 route | None; the package is data, not a service. The chart's container is `hello-world-rapp` and the registration bodies point at `http://hello-world-rapp:8080` |
| Call flow | [01-rapp-onboarding-to-deployment.md](../../docs/call-flows/01-rapp-onboarding-to-deployment.md) |
| Demo runbook | [DEMO_RUNBOOK.md](../../DEMO_RUNBOOK.md) sections 0-23 |
| Unit tests | None in this directory. The package is exercised by `tests_integration/test_cross_service.py::test_real_demo_csar_onboards_and_deploys` and `tests_integration/test_demo_runbook.py` |

## What it does

The package carries what a platform needs to take an rApp through its lifecycle:

- Onboarding validates the CSAR, reads the ASD for package identity and stores `manifest.yaml` / `capabilities.yaml` as the package's AI capabilities.
- Deployment creates an instance; NFO deploys the Helm chart (a stock `hashicorp/http-echo` container that prints a greeting).
- Bootstrap: the rApp container calls R1 Termination `/bootstrap`, then `bootstrap-complete` moves the instance from DEPLOYING to RUNNING and registers the package's `Files/Sme` provider and service API with SME.
- Operate: SME, DME, Intent, A1, AI platform and other modules are exercised using `hello-world-rapp` as the producer, consumer and requester identity.
- Retire: prime, terminate and delete the package.

## Design

There is no decision logic. The one convention worth knowing: this build uses a flattened identity, so `apfId`, `producerId`, `apiInvokerId`, `consumerId` and the module scope are all `hello-world-rapp`.

| Item | Value |
|---|---|
| ASD `application_name` / `application_version` | `hello-world-rapp` / `"1.0"` |
| ASD provider | `ai-ran-ref` |
| Deployment item | one Helm chart, `artifact_type: helm_chart`, `target_server: chartmuseum`, `item_id: 1` |
| Chart | `hello-world-chart` 0.1.0, one Deployment, image `hashicorp/http-echo:latest`, container port 5678, `replicaCount: 1` |
| DME producer callbacks | `http://hello-world-rapp:8080/health` and `/dme-jobs` |
| SME service API | `helloworld-api` v1 at `http://hello-world-rapp:8080/helloworld/v1`, `GET` on resource `helloworld` |

## Files

| File | Role |
|---|---|
| `TOSCA-Metadata/TOSCA.meta` | CSAR entry point (`Entry-Definitions: Definitions/asd.yaml`) |
| `Definitions/asd.yaml` | ASD: package identity and the Helm deployment item |
| `manifest.yaml` | Only `manifestVersion` and `aiRuntimeSdkVersion`; no AI part |
| `capabilities.yaml` | Provides `data` (its DME type); consumes `data` (its own type) and `platform` (SME provider and API set) |
| `Artifacts/Deployment/HELM/hello-world-chart-0.1.0.tgz` | The Helm chart |
| `Files/Sme/providers/provider.json` | SME provider (APF) registration body |
| `Files/Sme/serviceapis/api-set.json` | SME service API description for `helloworld-api` |
| `Files/Sme/invokers/invoker.json` | SME invoker registration body (`apiInvokerId`, onboarding secret) |
| `Files/Dme/infoproducers/producer.json` | DME type registration for `hello-world-metrics` |
| `Files/Dme/infoconsumers/consumer.json` | DME data job; its `dmeTypeId` is a placeholder filled from the producer registration |
| `Files/Acm/definition/compositions.json` | ONAP ACM composition naming the Helm element |

## Package

Layout and field semantics: [RAPP_PACKAGING.md](../../docs/RAPP_PACKAGING.md).

- Declared: ASD with one Helm deployment item; `manifest.yaml` and `capabilities.yaml` (the optional extension files); `Files/Sme`, `Files/Dme`, `Files/Acm`.
- How the runbook uses `Files/`: onboarding stores `Files/Sme` raw and rApp Management registers it with SME at `bootstrap-complete` (section 5). The runbook also posts the same bodies by hand (section 4) to create the provider, invoker, service API and DME type identities that later sections reuse. Onboarding does not read `Files/Dme`; `Files/Acm` is read by nothing, since the build never calls ONAP ACM.
- Deliberately absent: `executionModes`, `autonomyModes`, `requiredServices` and `runtimeProfiles` in the manifest, so it onboards as a plain rApp; no model or `models`, `lifecycle`, `intent` or `analytics` capabilities.
- Rebuild the CSAR with `python3 smo/samples/build_csar.py hello-world-rapp` after editing this directory. `build_csar.py` excludes this README.

## Service API

None. The package starts no service of its own; the routes it uses belong to the platform modules the runbook calls.

## Run and test

There are no unit tests here. Follow the runbook against a running stack:

```bash
cd smo
docker compose up -d --build
python3 samples/build_csar.py hello-world-rapp          # only after editing the sample
docker compose cp samples/hello-world-rapp.csar r1-termination:/tmp/hello-world-rapp.csar
```

Then work through [DEMO_RUNBOOK.md](../../DEMO_RUNBOOK.md): section 1 serves the CSAR, section 2 onboards it, section 3 deploys it, sections 4-5 simulate bootstrap and reach RUNNING, sections 6-22 are optional module walk-throughs (there is no section 9), and section 23 retires the package. Every command runs inside the `r1-termination` container with `docker compose exec`.

## Limits

- The chart runs a generic echo image, not an rApp; the callback URLs on port 8080 are never served by it, so the runbook registers the identities by hand rather than relying on a live rApp.
- The invoker secret in `Files/Sme/invokers/invoker.json` is a demo value.
- The consumer's `dmeTypeId` must be filled in from the producer registration before use.
- ACM composition is descriptive only.
