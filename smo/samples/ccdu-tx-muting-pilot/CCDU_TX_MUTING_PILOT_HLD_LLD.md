# CCDU TX-Muting Energy-Saving rApp Pilot: HLD and LLD

## Existing TX Muting Model, O-RAN Integration, Exact YANG and OID Mapping

**Status:** Pilot (demonstrates rApp functionality on the ai-ran-ref SMO)  
**Use Case:** CCDU MIMO TX Path Muting  
**Decision Method:** Instantaneous threshold evaluation  
**R1 Data Delivery Mode:** `ONE_TIME`  
**CCDU Southbound Interface:** OID binary messages over UDP  
**Existing Vendor YANG Module:** `gnb_du_vs_tx_muting.yang`  
**Pilot code:** `smo/samples/ccdu-tx-muting-pilot/`  
**Branch:** `pilot/ccdu-tx-muting-rapp`  

## Document map

| Part | Sections | Content |
|---|---|---|
| HLD | §1, §2, §5, §6 | Purpose, architecture and interfaces, decisions, decision logic |
| LLD | §3, §4, §7, §8, Appendix A | Vendor YANG, YANG-to-OID mapping, call flows, schemas and payloads |
| Implementation | §9, §10 | What ai-ran-ref provides, the pilot code, demo steps and helper scripts |
| References | §11 | Standards and repository artifacts |

---

# 1. Purpose

This document defines a simplified Non-RT RIC Energy-Saving rApp demonstration for the existing CCDU MIMO TX Path Muting feature.

The rApp evaluates the latest available measurements for one CCDU cell and generates one of the following decisions:

```text
REDUCED_TX
    Enable TX muting
    Change 32T32R to 16T32R

FULL_TX
    Disable TX muting
    Change 16T32R to 32T32R

NO_CHANGE
    Preserve the current configuration
```

The demo does not require:

```text
Historical measurement collection
Continuous R1 data subscriptions
Traffic forecasting
Machine-learning model training
Lean-hour prediction
Multi-cell optimization
A1
E2
Near-RT RIC
xApps
```

---

# 2. O-RAN-Aligned Architecture

Aligned with the SMO reference implementation in `rsys-bshankar/ai-ran-ref` (`smo/`): rApp calls go through R1 Termination, DME is the single R1 data and action-mediation entry point, and RAN NF OAM is the only SMO module that speaks O1.

```text
+----------------------------------------------------------------------+
|                                 SMO                                  |
|                                                                      |
|  +---------------------------- Non-RT RIC ------------------------+  |
|  |   Energy-Saving rApp                                           |  |
|  +----------|-----------------------------------------------------+  |
|             |                                                        |
|             | R1  (HTTPS/JSON, OAuth2 Bearer)                        |
|             v                                                        |
|  +----------------------------------------------------------------+  |
|  | R1 Termination  (R1 gateway, token introspection via SME)      |  |
|  +------+-------------------------------+-------------------------+  |
|         |                               |                            |
|         v                               v                            |
|  +----------------+   +--------------------------------------+      |
|  | SME (CAPIF)    |   | DME (Data Management and Exposure)   |      |
|  | - discovery    |   | - DME types, producers, data jobs    |      |
|  | - invoker      |   | - data records (ingest / fetch)      |      |
|  |   onboarding   |   | - /actions: O1 action mediation      |      |
|  | - /oauth2/token|   +--------+-----------------^-----------+      |
|  +----------------+            |                 |                   |
|                  POST          |                 | DME producer      |
|                  /config-jobs  |                 | registration,     |
|                  (SMO-internal)|                 | job callback,     |
|                                v                 | data records      |
|  +----------------------------------------------------------------+  |
|  | RAN NF OAM  (SMO O1 MnS Consumer)                              |  |
|  | - O1AdaptorEndpoint / ManagedEntity registry + heartbeat       |  |
|  | - WriteConfigJob (CM write via NETCONF edit-config)            |  |
|  | - Config read-back (NETCONF get-config)                        |  |
|  | - PM subscriptions, PM reports, FM ingest (DME producer)       |  |
|  +-------------------------------+--------------------------------+  |
+----------------------------------|-----------------------------------+
                                   |
                                   | O1  (NETCONF RFC 6241 for CM,
                                   |      VES / file-based for FM/PM)
                                   v
                    +-------------------------------+
                    | CCDU O1 Adaptor               |
                    | (O1 MnS Producer)             |
                    |                               |
                    | - gnb_du_vs_tx_muting YANG    |
                    | - O1-to-OID translation       |
                    | - CNUM and counter mapping    |
                    | - State verification          |
                    +---------------+---------------+
                                    |
                                    | OID binary over UDP
                                    v
                    +-------------------------------+
                    | CCDU OAMManager               |
                    +---------------+---------------+
                                    |
                                    | OID binary over UDP
                                    v
                    +-------------------------------+
                    | CCDU OAMAgents and MRU        |
                    +-------------------------------+
```

In the pilot (§10), `mock-o1-adaptor` stands in for the CCDU O1 Adaptor, and nothing below it is emulated.

## 2.1 Interface summary

| From | To | Interface | Standard / reference | ai-ran-ref module |
|---|---|---|---|---|
| Energy-Saving rApp | R1 Termination | R1 | O-RAN.WG2.TS.R1GAP, R1AP | `r1-termination/` |
| R1 Termination | SME | R1 SME services (CAPIF) | 3GPP TS 29.222, R1AP | `sme/` |
| R1 Termination | DME | R1 DME services | R1AP, O-RAN-SC ICS API | `dme/` |
| DME | RAN NF OAM | SMO-internal (not standardized) | `R1Client` over HTTP/JSON | `dme/` -> `ran-nf-oam/` |
| RAN NF OAM | DME | SMO-internal DME producer registration | O-RAN-SC ICS producer API | `ran-nf-oam/` -> `dme/` |
| RAN NF OAM | CCDU O1 Adaptor | O1 | O-RAN.WG10.O1-Interface, TS 28.532, RFC 6241 | `ran-nf-oam/` (`mock-o1-adaptor/` in the pilot) |
| CCDU O1 Adaptor | CCDU OAMManager | Vendor-internal | OID binary over UDP | Out of scope (vendor) |

---

# 3. Existing Vendor YANG Model

## 3.1 Artifact identity

```text
File:
oam_odu/yang/du/gnb_du_vs_tx_muting.yang

Module:
gnb_du_vs_tx_muting

Namespace:
urn:rdns:com:radisys:nr:gnbduVsTxMuting

Prefix:
duVsTxMuting

Revision:
2025-01-17
```

## 3.2 Augmented managed-object path

```text
ME
└── GNBDUFunction
    └── NRCellDU
        └── gnbCellDuVsCfg
            └── mimoTxMuting
                ├── txPathOffPattern
                ├── txMutingActivation
                └── txMutingFeatureEnable
```

## 3.3 Existing leaves

### `txPathOffPattern`

```text
Allowed values:
HORIZONTAL_PLANE
VERTICAL_PLANE

Default:
HORIZONTAL_PLANE
```

### `txMutingActivation`

```text
Allowed values:
MUTING_OFF
MUTING_ON

Default:
MUTING_OFF
```

### `txMutingFeatureEnable`

```text
Type:
boolean

Default:
true
```

---

# 4. Exact YANG-to-OID Mapping

## 4.1 TX path-off pattern

```text
YANG leaf:
txPathOffPattern

OID:
3.20.[0].15.[0].5000.137.1:TXPATHOFFPATTERN

Tree path:
DU_CELLVS_MIMOTXMUTING_TXPATHOFFPATTERN

Category:
DU_CELLVS

User-friendly name:
MIMOTXMUTING_TXPATHOFFPATTERN

Allowed values:
HORIZONTAL_PLANE
VERTICAL_PLANE
```

## 4.2 TX muting activation

```text
YANG leaf:
txMutingActivation

OID:
3.20.[0].15.[0].5000.137.2:TXMUTINGACTIVATION

Tree path:
DU_CELLVS_MIMOTXMUTING_TXMUTINGACTIVATION

Category:
DU_CELLVS

User-friendly name:
MIMOTXMUTING_TXMUTINGACTIVATION

Allowed values:
MUTING_OFF
MUTING_ON

Command references:
CMD_MIMO_TX_MUTE_ON_REQUEST
CMD_MIMO_TX_MUTE_OFF_REQUEST
```

## 4.3 TX muting feature enable

```text
YANG leaf:
txMutingFeatureEnable

OID:
3.20.[0].15.[0].5000.137.3:TXMUTINGFEATUREENABLE

Tree path:
DU_CELLVS_MIMOTXMUTING_TXMUTINGFEATUREENABLE

Category:
DU_CELLVS

User-friendly name:
MIMOTXMUTING_TXMUTINGFEATUREENABLE

Allowed values:
true
false
```

---

# 5. rApp-to-YANG Mapping

| rApp decision | Existing YANG leaf | Existing YANG value |
|---|---|---|
| `REDUCED_TX` | `txMutingActivation` | `MUTING_ON` |
| `FULL_TX` | `txMutingActivation` | `MUTING_OFF` |
| `NO_CHANGE` | No update | No PATCH |
| Horizontal muting | `txPathOffPattern` | `HORIZONTAL_PLANE` |
| Vertical muting | `txPathOffPattern` | `VERTICAL_PLANE` |
| Feature enabled | `txMutingFeatureEnable` | `true` |
| Feature disabled | `txMutingFeatureEnable` | `false` |

---

# 6. Decision Logic

Implemented by `smo/samples/ccdu-tx-muting-pilot/engine.py`.

## 6.1 Enable TX muting

```text
Current txMutingActivation == MUTING_OFF

AND

DL PRB utilization < 40 percent

AND

RRC-connected UE count < 10

AND

txMutingFeatureEnable == true

AND

MRU synchronization state == SYNCHRONIZED

AND

No blocking alarm is active

AND

All required measurements are VALID and fresh
```

## 6.2 Disable TX muting

```text
Current txMutingActivation == MUTING_ON

AND

(
    DL PRB utilization >= 42 percent

    OR

    RRC-connected UE count >= 12

    OR

    MRU synchronization state != SYNCHRONIZED

    OR

    A blocking alarm is active

    OR

    A required measurement is invalid or stale
)
```

The stale and missing cases are governed by `measurementPolicy.staleMeasurementAction` and `missingMeasurementAction` (A.5). The pilot sets both to `REQUEST_FULL_TX` so that §6.2 holds as written.

## 6.3 No change

```text
Current state does not require a transition
    ->
NO_CHANGE
```

Between the thresholds (for example PRB 41 percent while `MUTING_ON`) the result is `NO_CHANGE`. This is the hysteresis band that stops the cell from oscillating.

---

# 7. Startup Mermaid Flows

## Figure 1: CCDU OAMAgent Registration

~~~mermaid
sequenceDiagram
    autonumber
    participant L2 as CCDU L2 OAMAgent
    participant L3 as CCDU L3 OAMAgent
    participant MGR as CCDU OAMManager
    participant O1P as CCDU O1 Adaptor (O1 MnS Producer)

    L2->>MGR: UDP REGISTER_REQ_3
    Note right of L2: {
    Note right of L2: "messageType": "REGISTER",
    Note right of L2: "applicationId": 3,
    Note right of L2: "operationType": "REGISTER_REQ_3",
    Note right of L2: "operationId": "0x00000001",
    Note right of L2: "pmCategories": [
    Note right of L2: {
    Note right of L2: "categoryId": 20,
    Note right of L2: "nameStr": "RadioResourceUsage"
    Note right of L2: }
    Note right of L2: ]
    Note right of L2: }

    MGR-->>L2: UDP REGISTER_RESP_3
    Note left of MGR: {
    Note left of MGR: "operationId": "0x00000001",
    Note left of MGR: "responseCode": 0,
    Note left of MGR: "cnumMapping": [
    Note left of MGR: {
    Note left of MGR: "internal": 1,
    Note left of MGR: "external": 101
    Note left of MGR: }
    Note left of MGR: ]
    Note left of MGR: }

    L3->>MGR: UDP REGISTER_REQ_3
    Note right of L3: {
    Note right of L3: "applicationId": 4,
    Note right of L3: "operationType": "REGISTER_REQ_3",
    Note right of L3: "operationId": "0x00000002",
    Note right of L3: "cmCategories": [
    Note right of L3: {
    Note right of L3: "nameStr": "DU_CELLVS"
    Note right of L3: }
    Note right of L3: ]
    Note right of L3: }

    MGR-->>L3: UDP REGISTER_RESP_3
    Note left of MGR: {
    Note left of MGR: "operationId": "0x00000002",
    Note left of MGR: "responseCode": 0
    Note left of MGR: }

    O1P->>MGR: UDP REGISTER_REQ_3
    Note right of O1P: {
    Note right of O1P: "applicationId": 20,
    Note right of O1P: "operationType": "REGISTER_REQ_3",
    Note right of O1P: "operationId": "0x00000003",
    Note right of O1P: "subscriptions": [
    Note right of O1P: "CFG",
    Note right of O1P: "PERF",
    Note right of O1P: "FAULT"
    Note right of O1P: ]
    Note right of O1P: }

    MGR-->>O1P: UDP REGISTER_RESP_3
    Note left of MGR: {
    Note left of MGR: "operationId": "0x00000003",
    Note left of MGR: "responseCode": 0,
    Note left of MGR: "cnumMapping": [
    Note left of MGR: {
    Note left of MGR: "internal": 1,
    Note left of MGR: "managedCell": "Cell-101"
    Note left of MGR: }
    Note left of MGR: ]
    Note left of MGR: }
~~~

## Figure 2: Initial Configuration Reconciliation

~~~mermaid
sequenceDiagram
    autonumber
    participant O1P as CCDU O1 Adaptor (O1 MnS Producer)
    participant MGR as CCDU OAMManager
    participant L3 as CCDU L3 OAMAgent

    O1P->>MGR: Read mimoTxMuting
    Note right of O1P: operationType: GET_REQ
    Note right of O1P: operationId: 0x02000001
    Note right of O1P: Category: DU_CELLVS
    Note right of O1P: CNUM: 1

    MGR->>L3: Route OID GET_REQ

    L3-->>MGR: Return current values
    Note left of L3: {
    Note left of L3: "txMutingFeatureEnable": true,
    Note left of L3: "txPathOffPattern": "HORIZONTAL_PLANE",
    Note left of L3: "txMutingActivation": "MUTING_OFF"
    Note left of L3: }

    MGR-->>O1P: Return current values

    O1P->>O1P: Store CNUM and YANG state
~~~

In the pilot, step 00 writes this initial state into `mock-o1-adaptor` with one RAN NF OAM config job (`requestedBy: ccdu-initial-reconciliation`).

## Figure 3: O1 Adaptor Endpoint Registration and Heartbeat

ai-ran-ref replaces MnS Registry NRM polling (TS 28.623) with O1 Adaptor self-registration into RAN NF OAM. The endpoint starts in `DISCOVERED` and needs a heartbeat to reach `ACTIVE`. CM writes are only dispatched to `ACTIVE` endpoints.

~~~mermaid
sequenceDiagram
    autonumber
    participant O1P as CCDU O1 Adaptor (O1 MnS Producer)
    participant NFOAM as RAN NF OAM (O1 MnS Consumer)

    O1P->>NFOAM: POST /o1-adaptor-endpoints
    Note right of O1P: managedElementRef: ccdu-001
    Note right of O1P: o1Protocol: NETCONF
    Note right of O1P: transport: http-mock (ssh or tls for a real CCDU)
    Note right of O1P: adaptorUri: http://mock-o1-adaptor:8000/edit-config
    Note right of O1P: vendorName: radisys-ccdu

    NFOAM->>NFOAM: Create O1AdaptorEndpoint and ManagedEntity
    Note right of NFOAM: EndpointHealth state: DISCOVERED

    NFOAM-->>O1P: HTTP 201 Created

    O1P->>NFOAM: POST /o1-adaptor-endpoints/id/heartbeat
    Note right of O1P: Reference: TS28532_HeartbeatNtf.yaml

    NFOAM->>NFOAM: EndpointHealth DISCOVERED to ACTIVE
~~~

## Figure 4: R1 Bootstrap, Invoker Onboarding and Authentication

Not exercised by the pilot, which runs inside the compose network like the other DEMO_RUNBOOK.md demos (§10.5).

~~~mermaid
sequenceDiagram
    autonumber
    participant APP as Energy-Saving rApp
    participant R1T as R1 Termination
    participant SME as SME (CAPIF)

    APP->>R1T: Onboard API invoker
    Note right of APP: Standard: 3GPP TS 29.222 CAPIF
    Note right of APP: Body: apiInvokerPublicKey
    R1T->>SME: Forward invoker onboarding
    SME-->>R1T: HTTP 201 Created
    Note left of SME: apiInvokerId: server-generated
    Note left of SME: onboardingSecret: server-generated, hashed at rest
    R1T-->>APP: Return apiInvokerId and onboardingSecret

    APP->>R1T: POST /oauth2/token
    Note right of APP: Standard: RFC 6749
    Note right of APP: Grant type: client_credentials
    R1T->>SME: Validate client credentials
    SME-->>R1T: Access token issued
    R1T-->>APP: HTTP 200 OK
    Note left of R1T: Token type: Bearer

    APP->>R1T: Discover DME service API
    R1T->>SME: POST /oauth2/introspect
    Note right of R1T: Standard: RFC 7662
    SME-->>R1T: active: true
    R1T->>SME: Discover published service APIs
    SME-->>R1T: DME API endpoint
    R1T-->>APP: HTTP 200 OK
~~~

---

# 8. Runtime Mermaid Flows

## Figure 5a: RAN NF OAM Registers as DME Producer

`POST /pm-subscriptions` is a DME-producer registration wrapper: each PM counter becomes the DME type `RAN.PMCounters.<counter>`.

~~~mermaid
sequenceDiagram
    autonumber
    participant NFOAM as RAN NF OAM
    participant DME as DME

    NFOAM->>DME: POST /production-capabilities (per subscribed counter)
    Note right of NFOAM: typeName: RAN.PMCounters.DL_PRB_UTILIZATION
    Note right of NFOAM: typeName: RAN.PMCounters.RRC_CONNECTED_UE
    Note right of NFOAM: typeName: RAN.PMCounters.MRU_SYNC_STATE
    Note right of NFOAM: producerId: ran-nf-oam
    Note right of NFOAM: jobCallbackUrl: ran-nf-oam /dme-jobs
    Note right of NFOAM: producerHealthCallbackUrl: ran-nf-oam /health
    DME-->>NFOAM: HTTP 201 Created
~~~

## Figure 5b: One-Time Data Job

~~~mermaid
sequenceDiagram
    autonumber
    participant APP as Energy-Saving rApp
    participant DME as DME
    participant NFOAM as RAN NF OAM

    APP->>DME: POST /data-jobs (one per counter)
    Note right of APP: dataDeliveryMode: ONE_TIME
    Note right of APP: dataDeliveryMethod: PULL_HTTP
    Note right of APP: lifecycleStage: INFERENCE
    Note right of APP: consumerId: ccdu-tx-muting-pilot
    Note right of APP: productionJobDefinition: JIO_CCDU_InstantaneousJob

    DME->>DME: Validate productionJobDefinition against dataProductionSchema
    DME->>DME: Check DataOffer delivery method
    DME->>DME: Reject DIGITAL_TWIN with INFERENCE

    DME->>NFOAM: POST jobCallbackUrl /dme-jobs
    NFOAM-->>DME: HTTP 200 OK

    DME-->>APP: HTTP 201 Created
    Note left of DME: dataJobId per counter
~~~

## Figure 6: Instantaneous Measurement Delivery

PM arrives at RAN NF OAM (`POST /pm-reports`) and is fanned out as DME records to every open job on the counter's type. The rApp pulls them. TX-muting state is read over O1 and alarms from RAN NF OAM.

~~~mermaid
sequenceDiagram
    autonumber
    participant CCDU as CCDU PM reporting
    participant NFOAM as RAN NF OAM
    participant DME as DME
    participant APP as Energy-Saving rApp

    CCDU->>NFOAM: POST /pm-reports
    Note right of CCDU: DL_PRB_UTILIZATION 18.4, RRC_CONNECTED_UE 4, MRU_SYNC_STATE 1
    NFOAM->>DME: POST /data-jobs/id/records (per open job)
    DME-->>NFOAM: HTTP 201 Created

    APP->>DME: GET /data-jobs/id/records
    DME-->>APP: Latest record per counter for Cell 101

    APP->>NFOAM: GET /managed-entities/ccdu-001/config?managed_function_ref=NRCellDU=101
    NFOAM-->>APP: NETCONF get-config result
    Note left of NFOAM: txMutingFeatureEnable: true
    Note left of NFOAM: txPathOffPattern: HORIZONTAL_PLANE
    Note left of NFOAM: txMutingActivation: MUTING_OFF

    APP->>NFOAM: GET /alarms?managed_element_ref=ccdu-001
    NFOAM-->>APP: Active alarms on the element or the cell

    APP->>APP: Validate freshness (900 s) and quality
~~~

## Figure 7: Instantaneous Threshold Evaluation

~~~mermaid
sequenceDiagram
    autonumber
    participant APP as Energy-Saving rApp

    APP->>APP: Evaluate activation criteria
    Note right of APP: Current state is MUTING_OFF: true
    Note right of APP: PRB below 40 percent: true
    Note right of APP: UE count below 10: true
    Note right of APP: Feature enabled: true
    Note right of APP: MRU synchronized: true
    Note right of APP: Blocking alarm present: false

    APP->>APP: Generate REDUCED_TX decision
    Note right of APP: Decision ID: ES-PILOT-0001
    Note right of APP: YANG value: MUTING_ON
    Note right of APP: Pattern: HORIZONTAL_PLANE
~~~

## Figure 8: DME O1 Action Mediation

There is no separate "R1 Configuration Management" function in ai-ran-ref. The rApp decision enters through DME `/actions` (call flow 03, Path B), which records provenance and forwards to RAN NF OAM `POST /config-jobs`. The DME-to-RAN NF OAM hop is SMO-internal and not standardized by O-RAN.

~~~mermaid
sequenceDiagram
    autonumber
    participant APP as Energy-Saving rApp
    participant DME as DME
    participant NFOAM as RAN NF OAM (O1 MnS Consumer)

    APP->>DME: POST /actions
    Note right of APP: X-Correlation-ID: ES-PILOT-0001
    Note right of APP: actionId: rApp-generated (idempotency key)
    Note right of APP: requestedBy: ccdu-tx-muting-pilot
    Note right of APP: className: NRCellDU, managedFunctionRef: NRCellDU=101
    Note right of APP: txMutingFeatureEnable: true
    Note right of APP: txPathOffPattern: HORIZONTAL_PLANE
    Note right of APP: txMutingActivation: MUTING_ON

    DME->>DME: Create DmeActionRecord
    Note right of DME: sourceContext and correlation id recorded

    DME->>NFOAM: POST /config-jobs
    Note right of DME: SMO-internal via R1Client
    NFOAM-->>DME: WriteConfigJob ID and status
    DME-->>APP: actionId, forwardedJobId, status
~~~

## Figure 9: O1 Provisioning Request

~~~mermaid
sequenceDiagram
    autonumber
    participant NFOAM as RAN NF OAM (O1 MnS Consumer)
    participant O1P as CCDU O1 Adaptor (O1 MnS Producer)

    NFOAM->>NFOAM: MSAC gate and vendor data-model check
    NFOAM->>NFOAM: Decompose into per-ME WriteConfigSubChange
    NFOAM->>NFOAM: Check O1AdaptorEndpoint is ACTIVE

    NFOAM->>O1P: NETCONF edit-config
    Note right of NFOAM: Standard: RFC 6241 section 7.2
    Note right of NFOAM: operation: merge
    Note right of NFOAM: managed-object ref ccdu-001, function-ref NRCellDU=101
    Note right of NFOAM: txMutingFeatureEnable: true
    Note right of NFOAM: txPathOffPattern: HORIZONTAL_PLANE
    Note right of NFOAM: txMutingActivation: MUTING_ON
    O1P-->>NFOAM: rpc-reply ok

    O1P->>O1P: Validate feature support
    O1P->>O1P: Map cell identity to CNUM
    O1P->>O1P: Map YANG leaves to OID

    NFOAM->>O1P: NETCONF get-config (rApp read-back)
    O1P-->>NFOAM: txMutingActivation: MUTING_ON
~~~

## Figure 10: O1-to-OID Translation

~~~mermaid
sequenceDiagram
    autonumber
    participant O1P as CCDU O1 Adaptor (O1 MnS Producer)
    participant MGR as CCDU OAMManager

    O1P->>O1P: Map txPathOffPattern
    Note right of O1P: OID suffix: 137.1
    Note right of O1P: UFN: MIMOTXMUTING_TXPATHOFFPATTERN
    Note right of O1P: Value: HORIZONTAL_PLANE

    O1P->>O1P: Map txMutingActivation
    Note right of O1P: OID suffix: 137.2
    Note right of O1P: UFN: MIMOTXMUTING_TXMUTINGACTIVATION
    Note right of O1P: Value: MUTING_ON

    O1P->>O1P: Map txMutingFeatureEnable
    Note right of O1P: OID suffix: 137.3
    Note right of O1P: UFN: MIMOTXMUTING_TXMUTINGFEATUREENABLE
    Note right of O1P: Value: true

    O1P->>MGR: Send OID CFG request over UDP
    Note right of O1P: Category: DU_CELLVS
    Note right of O1P: CNUM: target-cell CNUM
    Note right of O1P: operationId: 0x02000001
~~~

## Figure 11: Radio Execution

~~~mermaid
sequenceDiagram
    autonumber
    participant MGR as CCDU OAMManager
    participant AGT as Configuration OAMAgent
    participant MRU as MRU

    MGR->>AGT: Route OID CFG request
    Note right of MGR: Category: DU_CELLVS
    Note right of MGR: operationId: 0x02000001

    AGT->>MRU: Apply TX muting
    Note right of AGT: Requested mode: 16T32R
    Note right of AGT: Pattern: HORIZONTAL_PLANE

    MRU-->>AGT: Configuration result
    Note left of MRU: Result: SUCCESS
    Note left of MRU: Applied TX paths: 16
    Note left of MRU: Active RX paths: 32

    AGT-->>MGR: RESPONSE_MSG
    Note left of AGT: responseCode: 0
    Note left of AGT: numberOfErrors: 0
~~~

---

# 9. Implementation Mapping to ai-ran-ref

Checked against `main` at commit `599f9e0`.

Status legend:

```text
EXISTS   Route/behavior already in smo/ - used as-is by the pilot
PILOT    Implemented by the pilot (smo/samples/ccdu-tx-muting-pilot/)
EXTEND   Exists in ai-ran-ref but needs configuration or a change for a real CCDU
VENDOR   Outside ai-ran-ref scope - CCDU/vendor side
```

| Figure | Step | Status | ai-ran-ref module / route | Notes |
|---|---|---|---|---|
| 1 | OAMAgent and O1 Adaptor UDP registration | VENDOR | - | CCDU OAMManager internal |
| 2 | Initial config read via OID GET_REQ | VENDOR | - | Pilot seeds the same state with one config job |
| 3 | O1 Adaptor self-registration | EXISTS | `ran-nf-oam` `POST /o1-adaptor-endpoints` | `DISCOVERED`, then `ACTIVE` on heartbeat |
| 3 | Vendor data model `gnb_du_vs_tx_muting` | EXTEND | `ran-nf-oam` `vendors.py`, `scripts/ingest_yang_schema.py` | Vendor capability registry with OWN/SPEC/COMBINED conformance exists. Pilot vendor `radisys-ccdu` has no capability record, so the data-model check is skipped |
| 4 | CAPIF onboarding, `/oauth2/token`, introspection, discovery | EXISTS | `sme`, `r1-termination` | Not used by the pilot (§10.5) |
| 4 | Per-element / per-attribute authorization (A.6) | EXTEND | `ran-nf-oam` `msac.py` (TS 28.319) | Identity/Role/AccessRule model exists. Pilot registers no identity |
| 5a | PM counters as DME producer types | EXISTS | `ran-nf-oam` `POST /pm-subscriptions` | One DME type per counter |
| 5b | Data job, schema validation, eligibility, producer callback | EXISTS | `dme` `POST /data-jobs` | `ONE_TIME` accepted. Jobs are not auto-completed |
| 6 | PM report fan-out to DME records | EXISTS | `ran-nf-oam` `POST /pm-reports` | Pilot plays the CCDU PM source |
| 6 | Pull records | EXISTS | `dme` `GET /data-jobs/{id}/records` | - |
| 6 | Current TX-muting state | EXISTS | `ran-nf-oam` `GET /managed-entities/{me}/config` | NETCONF `get-config` |
| 6 | Alarms | EXISTS | `ran-nf-oam` `GET /alarms` | DME `RAN.FaultRecords` path not used by the pilot |
| 6 | Freshness and quality checks | PILOT | `engine.quality()` | - |
| 7 | Threshold evaluation, hysteresis, decision record | PILOT | `engine.evaluate()` | A.5 config in `thresholds.json` |
| 8 | `POST /actions` -> `DmeActionRecord` -> `POST /config-jobs` | EXISTS | `dme`, `ran-nf-oam` | `actionId` idempotency, `X-Correlation-ID`, `sourceContext` |
| 9 | MSAC, data-model check, `ACTIVE` gate, waves, `edit-config` merge | EXISTS | `ran-nf-oam` | Transports: http-mock, NETCONF over SSH (RFC 6242), NETCONF over TLS (RFC 7589), RESTCONF |
| 9 | Vendor namespace and `gnbCellDuVsCfg/mimoTxMuting` subtree | EXTEND | `ran-nf-oam` `yang_payload.py` | Real-model payloads are chosen per ssh endpoint with `?model=<profile>`. A `gnb_du_vs_tx_muting` profile must be added to `PROFILES`. Pilot writes the three leaves flat on `NRCellDU=101` |
| 9 | Answer `edit-config` / `get-config` | EXISTS (pilot) / VENDOR (real) | `mock-o1-adaptor` | Mock keeps any attribute per managed function |
| 9 | Read-back verification, retry, rollback | PILOT | `pilot.py` `_verify()` | A.5 `executionPolicy` |
| 10 | YANG-to-OID translation | VENDOR | - | - |
| 11 | OAMManager -> OAMAgent -> MRU | VENDOR | - | - |

## 9.1 From pilot to a real CCDU

```text
1. ran-nf-oam   Onboard vendor radisys-ccdu and ingest gnb_du_vs_tx_muting.yang
                (scripts/ingest_yang_schema.py) so writes are checked against it
2. ran-nf-oam   Add a gnb_du_vs_tx_muting profile to yang_payload.py PROFILES and register
                the real CCDU O1 Adaptor with transport ssh and ?model=<profile>
3. CCDU         O1 Adaptor answers edit-config / get-config and maps leaves to OID (§4)
4. CCDU         PM via file or streaming reporting instead of POST /pm-reports
5. rApp         Package as a CSAR and compose service like samples/energy-saving-rapp,
                calling the SMO through R1 Termination with an SME token
6. ran-nf-oam   MSAC identity for the rApp limited to NRCellDU and the three leaves (A.6)
```

---

# 10. Pilot Implementation and Demo

## 10.1 What runs

| Role in §2 | Pilot |
|---|---|
| Energy-Saving rApp | `pilot.py` + `engine.py`, run inside the `r1-termination` container |
| R1 Termination, SME | Started, healthy, not used by the pilot's own calls |
| DME | `dme` |
| RAN NF OAM | `ran-nf-oam`, `ran-nf-oam-worker` |
| CCDU O1 Adaptor | `mock-o1-adaptor` (NETCONF over the http-mock transport) |
| CCDU PM source | `pilot.py` posting `POST /pm-reports` |
| CCDU fault source | `pilot.py` posting `POST /alarms/ingest` |
| OAMManager, OAMAgents, MRU | Not emulated |

## 10.2 Code layout

```text
smo/samples/ccdu-tx-muting-pilot/
├── CCDU_TX_MUTING_PILOT_HLD_LLD.md   This document
├── engine.py            §6 decision logic, A.5.4 hysteresis check (pure functions)
├── pilot.py             Demo steps 00-08: reads, decision, DME action, read-back
├── thresholds.json      A.5 threshold configuration
├── tests/
│   └── test_engine.py   Unit tests for engine.py
└── scripts/
    ├── lib.sh           Shared paths, service list, copy-into-stack helper
    ├── start.sh         Secrets (once), start services, wait for health, copy pilot
    ├── run_demo.sh      Re-copy the pilot and run steps inside the compose network
    ├── stop.sh          Stop the stack (--down removes containers, keeps volumes)
    ├── cleanup.sh       Reset pilot state (--purge: whole stack incl. database)
    └── commit_push.sh   Commit pilot + this document and push the branch
```

## 10.3 Demo steps

| Step | What it does | Expected result |
|---|---|---|
| 00 | Register `ccdu-001` (vendor `radisys-ccdu`) behind `mock-o1-adaptor`, heartbeat, subscribe 3 PM counters, seed Figure 2 state | Endpoint `ACTIVE`, `NRCellDU=101` reads `MUTING_OFF`, feature `true` |
| 01 | Load and check `thresholds.json`, open one `ONE_TIME` `PULL_HTTP` `INFERENCE` data job per counter | 3 data job ids |
| 02 | PRB 18.4 %, 4 UEs, MRU synchronized | `REDUCED_TX`, DME action `COMPLETED`, read-back `VERIFIED` `MUTING_ON` |
| 03 | Show the DME action, RAN NF OAM config job and mock O1 Adaptor running config | `correlationId` = decision id, job `COMPLETED`, mock shows the three leaves |
| 04 | PRB 41 %, 8 UEs while `MUTING_ON` | `NO_CHANGE` (`LOAD_WITHIN_HYSTERESIS`), no O1 write |
| 05 | PRB 45 % | `FULL_TX` (`PRB_HIGH`), read-back `MUTING_OFF` |
| 06 | Alarm 13325 `L1_FH_COMM_DOWN_ERROR` on `NRCellDU=101`, PRB 16.2 %, 3 UEs | `NO_CHANGE` (`BLOCKING_ALARM`), alarm cleared afterwards |
| 07 | Low load, then MRU not synchronized | `REDUCED_TX`, then `FULL_TX` (`MRU_NOT_SYNCHRONIZED`) |
| 08 | Audit | Decision table and the count of DME actions by `ccdu-tx-muting-pilot` |

Ids are kept between runs in `/tmp/ccdu-tx-muting-pilot.json` inside the container (`PILOT_STATE`). `PILOT_ME`, `PILOT_CELL`, `PILOT_ADAPTOR_URI` and `PILOT_THRESHOLDS` override the defaults.

## 10.4 Running it

Prerequisites: a Linux host (or WSL2) with Docker Engine and the Compose plugin, Python 3 (for `scripts/init_secrets.sh`) and git.

```bash
cd smo/samples/ccdu-tx-muting-pilot
scripts/start.sh                 # pilot services only; FULL_STACK=1 for everything incl. GUI
scripts/run_demo.sh              # steps 00-08
scripts/run_demo.sh 04 05        # chosen steps
scripts/cleanup.sh               # reset the pilot, then run again from 00
scripts/stop.sh                  # stop (scripts/stop.sh --down removes containers)
scripts/cleanup.sh --purge       # remove the whole stack and its database (asks first)
scripts/commit_push.sh "msg"     # commit pilot + this document, push the branch
```

Unit tests (no stack needed):

```bash
cd smo/samples/ccdu-tx-muting-pilot && python3 -m pytest tests/ -q
```

## 10.5 Pilot simplifications

| Area | Design (§2-§8) | Pilot |
|---|---|---|
| R1 access | rApp -> R1 Termination with SME Bearer token | Direct calls to service hostnames inside the compose network, as in DEMO_RUNBOOK.md §24 |
| Packaging | CSAR onboarded, rApp instance in rApp Management | Script, not onboarded |
| DME data type | One composite type `tx-muting-instantaneous-input` (A.7) | One type per PM counter (`RAN.PMCounters.<counter>`) |
| MRU synchronization | Measurement `mruSynchronizationState` | PM counter `MRU_SYNC_STATE` (1 = SYNCHRONIZED) |
| Alarms | DME `RAN.FaultRecords` | RAN NF OAM `GET /alarms` |
| TX-muting state | Read from DME | NETCONF `get-config` through RAN NF OAM |
| YANG path | `NRCellDU/gnbCellDuVsCfg/mimoTxMuting` in namespace `urn:rdns:com:radisys:nr:gnbduVsTxMuting` | Leaves written flat on `NRCellDU=101` |
| Data-model check | Against `gnb_du_vs_tx_muting.yang` | Skipped (no vendor capability record) |
| Stale or missing data while muted | A.5.3 example: `NO_ACTION` | `REQUEST_FULL_TX`, to follow §6.2 |

## 10.6 Verification status

The pilot was written against `main` at `599f9e0`: routes, request models and response shapes were read from the source. Neither the unit tests nor the demo have been run yet. Run `python3 -m pytest tests/ -q` and `scripts/start.sh && scripts/run_demo.sh` on a Docker host before relying on the results in §10.3.

---

# 11. Standards and Artifact References

## O-RAN

```text
O-RAN.WG2.TS.R1GAP-R004
O-RAN R1 Interface:
General Aspects and Principles

O-RAN.WG2.TS.R1AP-R004
O-RAN R1 Interface:
Application Protocols for R1 Services

O-RAN.WG2.TS.R1TD-R004
O-RAN Type Definitions for R1 Services

O-RAN.WG2.TS.R1UCR-R004
O-RAN R1 Interface:
Use Cases and Requirements

O-RAN.WG10.O1-Interface
O-RAN Operations and Maintenance Interface Specification
(SMO = O1 MnS Consumer, managed element = O1 MnS Producer)

O-RAN.WG10.OAM-Architecture
O-RAN Operations and Maintenance Architecture

O-RAN WG10 O1NRM YANGs
ai-ran-ref: specs/O-RAN-WG10-O1NRM-YANGs/

O-RAN WG5 O-DU MP YANGs
ai-ran-ref: specs/O-RAN-WG5-O-DU-MP-YANGs/
(per-vendor own/spec/combined conformance)
```

## 3GPP

```text
3GPP TS 28.533
Management and orchestration: Architecture framework
(defines MnS, MnS Producer, MnS Consumer)

3GPP TS 29.222
Common API Framework (CAPIF) - used by SME

3GPP TS 28.532
Generic Management Services
OpenAPI: TS28532_ProvMnS.yaml
OpenAPI: TS28532_HeartbeatNtf.yaml

3GPP TS 28.541
5G Network Resource Model
OpenAPI: TS28541_NrNrm.yaml

3GPP TS 28.623
Generic NRM Solution Set
OpenAPI: TS28623_MnSRegistryNrm.yaml

3GPP TS 28.111
Fault Management
OpenAPI:
TS28111_FaultNrm.yaml
TS28111_FaultNotifications.yaml

3GPP TS 28.550
Performance assurance
OpenAPI: TS28550_PerfMeasJobCtrlMnS.yaml

3GPP TS 28.319
Management service access control (MSAC)

ai-ran-ref location for the OpenAPI files:
specs/5G_APIs/
```

## IETF

```text
RFC 6241  NETCONF (edit-config / get-config used by RAN NF OAM)
RFC 6242  NETCONF over SSH
RFC 7589  NETCONF over TLS
RFC 6749  OAuth 2.0 (client_credentials at SME /oauth2/token)
RFC 6750  Bearer token usage
RFC 7662  Token introspection (R1 Termination to SME)
RFC 7807  Problem Details
```

## Reference implementation

```text
https://github.com/rsys-bshankar/ai-ran-ref

smo/r1-termination/   R1 gateway, OAuth2 enforcement
smo/sme/              CAPIF service exposure, invoker onboarding, tokens
smo/dme/              DME types, data jobs, data records, /actions
smo/ran-nf-oam/       O1 MnS Consumer: NETCONF/RESTCONF CM, read-back, PM, FM, MSAC
smo/mock-o1-adaptor/  O1 Adaptor test double
smo/samples/energy-saving-rapp/       Reference rApp (cell sleep) the pilot follows
smo/samples/ccdu-tx-muting-pilot/     This pilot
smo/docs/call-flows/03-config-write-with-schema-check.md
smo/docs/call-flows/22-energy-saving-closed-loop.md
smo/DEMO_RUNBOOK.md
```

---

# Appendix A: Examples for Vendor Artifacts to Create

## A.1 Purpose

This appendix defines complete example artifacts for the CCDU instantaneous threshold-based Energy-Saving rApp demo.

The vendor artifacts supplement the standard O-RAN R1 and O1 models. They do not replace the standard service envelopes, procedures, or managed-object hierarchy.

The artifacts are:

```text
JIO_CCDU_InstantaneousJob.schema.json

JIO_CCDU_InstantaneousValues.schema.json

JIO_CCDU_EnergySavingThresholds.schema.json

JIO_CCDU_EnergySaving_OAuth_Profile.yaml
```

The existing TX muting YANG model remains:

```text
gnb_du_vs_tx_muting.yang
```

A second TX muting YANG module shall not be created.

---

## A.2 Artifact-to-Standard Mapping

### A.2.1 Instantaneous data-job schema

```text
Vendor artifact:
JIO_CCDU_InstantaneousJob.schema.json

Standard R1 envelope:
DataJobInfo

Standard extension point:
DataJobInfo.productionJobDefinition

Standard artifact:
O-RAN.WG2.TS.R1AP-R004
ETSI TS 104 231
```

### A.2.2 Instantaneous measurement-delivery schema

```text
Vendor artifact:
JIO_CCDU_InstantaneousValues.schema.json

Standard registration object:
DeliverySchema

Standard delivery procedure:
R1 HTTP Push Data

Standard artifacts:
O-RAN.WG2.TS.R1AP-R004
O-RAN.WG2.TS.R1TD-R004
ETSI TS 104 231
ETSI TS 104 232
```

### A.2.3 Energy-saving threshold schema

```text
Vendor artifact:
JIO_CCDU_EnergySavingThresholds.schema.json

Standard extension point:
Local rApp configuration

Standard decision algorithm:
None mandated by O-RAN

Purpose:
Activation thresholds
Deactivation thresholds
Measurement-quality policy
Measurement-freshness policy
Alarm policy
OID execution policy
Readback policy
```

### A.2.4 OAuth deployment profile

```text
Vendor artifact:
JIO_CCDU_EnergySaving_OAuth_Profile.yaml

Standards:
IETF RFC 6749
IETF RFC 6750

Purpose:
OAuth client definition
R1 scopes
Managed-element authorization
Managed-object authorization
Attribute-level authorization
Safety policy
Audit policy
```

---

# A.3 `JIO_CCDU_InstantaneousJob.schema.json`

## A.3.1 Purpose

This JSON Schema defines the CCDU-specific content placed inside:

```text
DataJobInfo.productionJobDefinition
```

It allows an rApp to request the latest available measurements for one CCDU cell.

## A.3.2 Complete schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.jio.com/ccdu/energy-saving/instantaneous-job/1.0.0",
  "title": "CCDU Instantaneous Energy-Saving Data Job",
  "description": "Production-job definition for requesting the latest available CCDU measurements.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "target",
    "measurementNames",
    "sampleSelection",
    "maximumSampleAgeSeconds"
  ],
  "properties": {
    "target": {
      "$ref": "#/$defs/Target"
    },
    "measurementNames": {
      "type": "array",
      "description": "Measurements required by the instantaneous threshold algorithm.",
      "minItems": 1,
      "uniqueItems": true,
      "items": {
        "$ref": "#/$defs/MeasurementName"
      }
    },
    "sampleSelection": {
      "type": "string",
      "description": "Rule used to select the measurement sample.",
      "enum": [
        "LATEST_AVAILABLE"
      ]
    },
    "maximumSampleAgeSeconds": {
      "type": "integer",
      "description": "Maximum permitted age of the returned measurements.",
      "minimum": 1,
      "maximum": 3600,
      "default": 900
    },
    "includeSourceIdentifiers": {
      "type": "boolean",
      "description": "Includes CCDU source identifiers for troubleshooting.",
      "default": false
    }
  },
  "$defs": {
    "Target": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "ranNodeId",
        "managedElementId",
        "gnbDuFunctionId",
        "cellId"
      ],
      "properties": {
        "ranNodeId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "examples": [
            "CCDU-001"
          ]
        },
        "managedElementId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "examples": [
            "CCDU-001"
          ]
        },
        "gnbDuFunctionId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "examples": [
            "DU-1"
          ]
        },
        "cellId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "examples": [
            "Cell-101"
          ]
        },
        "distinguishedName": {
          "type": "string",
          "examples": [
            "ME=CCDU-001,GNBDUFunction=DU-1,NRCellDU=Cell-101"
          ]
        }
      }
    },
    "MeasurementName": {
      "type": "string",
      "enum": [
        "dlPrbUtilization",
        "rrcConnectedUeCount",
        "txMutingActivation",
        "txMutingFeatureEnable",
        "txPathOffPattern",
        "mruSynchronizationState",
        "blockingAlarmPresent"
      ]
    }
  },
  "examples": [
    {
      "target": {
        "ranNodeId": "CCDU-001",
        "managedElementId": "CCDU-001",
        "gnbDuFunctionId": "DU-1",
        "cellId": "Cell-101",
        "distinguishedName": "ME=CCDU-001,GNBDUFunction=DU-1,NRCellDU=Cell-101"
      },
      "measurementNames": [
        "dlPrbUtilization",
        "rrcConnectedUeCount",
        "txMutingActivation",
        "txMutingFeatureEnable",
        "txPathOffPattern",
        "mruSynchronizationState",
        "blockingAlarmPresent"
      ],
      "sampleSelection": "LATEST_AVAILABLE",
      "maximumSampleAgeSeconds": 900,
      "includeSourceIdentifiers": false
    }
  ]
}
```

## A.3.3 Example `DataJobInfo` as accepted by ai-ran-ref DME

The R1 envelope carries the vendor-defined production-job object under `productionJobDefinition`. Field names follow `dme` `DataJobRequest`.

```json
{
  "dataDeliveryMode": "ONE_TIME",
  "dmeTypeId": "<dmeTypeId of RAN.PMCounters.DL_PRB_UTILIZATION>",
  "dataDeliveryMethod": "PULL_HTTP",
  "consumerId": "ccdu-tx-muting-pilot",
  "lifecycleStage": "INFERENCE",
  "productionJobDefinition": {
    "target": {
      "ranNodeId": "ccdu-001",
      "managedElementId": "ccdu-001",
      "gnbDuFunctionId": "DU-1",
      "cellId": "101"
    },
    "measurementNames": [
      "dlPrbUtilization",
      "rrcConnectedUeCount",
      "mruSynchronizationState"
    ],
    "sampleSelection": "LATEST_AVAILABLE",
    "maximumSampleAgeSeconds": 900
  }
}
```

## A.3.4 Validation rules

```text
dataDeliveryMode:
Must be ONE_TIME

sampleSelection:
Must be LATEST_AVAILABLE

maximumSampleAgeSeconds:
Must be between 1 and 3600

target:
Must identify exactly one CCDU cell

measurementNames:
Must contain at least one supported measurement

includeSourceIdentifiers:
Should normally be false outside troubleshooting
```

---

# A.4 `JIO_CCDU_InstantaneousValues.schema.json`

## A.4.1 Purpose

This JSON Schema defines the one-time measurement payload delivered by DME to the rApp.

The payload includes:

```text
Measurement timestamp
Target CCDU and cell
Measurement quality
Measurement values
Optional CCDU source identifiers
Active blocking-alarm references
```

## A.4.2 Complete schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.jio.com/ccdu/energy-saving/instantaneous-values/1.0.0",
  "title": "CCDU Instantaneous Energy-Saving Measurements",
  "description": "One-time measurement payload consumed by the CCDU Energy-Saving rApp.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schemaVersion",
    "dataJobId",
    "target",
    "measurementTime",
    "measurements"
  ],
  "properties": {
    "schemaVersion": {
      "type": "string",
      "const": "1.0.0"
    },
    "dataJobId": {
      "type": "string",
      "minLength": 1,
      "maxLength": 256
    },
    "target": {
      "$ref": "#/$defs/Target"
    },
    "measurementTime": {
      "type": "string",
      "format": "date-time"
    },
    "producerTime": {
      "type": "string",
      "format": "date-time"
    },
    "measurements": {
      "type": "array",
      "minItems": 1,
      "items": {
        "oneOf": [
          {
            "$ref": "#/$defs/DlPrbUtilization"
          },
          {
            "$ref": "#/$defs/RrcConnectedUeCount"
          },
          {
            "$ref": "#/$defs/TxMutingActivation"
          },
          {
            "$ref": "#/$defs/TxMutingFeatureEnable"
          },
          {
            "$ref": "#/$defs/TxPathOffPattern"
          },
          {
            "$ref": "#/$defs/MruSynchronizationState"
          },
          {
            "$ref": "#/$defs/BlockingAlarmPresent"
          }
        ]
      }
    },
    "activeBlockingAlarms": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/AlarmReference"
      },
      "default": []
    }
  },
  "$defs": {
    "Target": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "ranNodeId",
        "managedElementId",
        "gnbDuFunctionId",
        "cellId"
      ],
      "properties": {
        "ranNodeId": {
          "type": "string"
        },
        "managedElementId": {
          "type": "string"
        },
        "gnbDuFunctionId": {
          "type": "string"
        },
        "cellId": {
          "type": "string"
        },
        "cnum": {
          "type": "integer",
          "minimum": 0
        },
        "distinguishedName": {
          "type": "string"
        }
      }
    },
    "Quality": {
      "type": "string",
      "enum": [
        "VALID",
        "STALE",
        "MISSING",
        "INVALID",
        "ESTIMATED"
      ]
    },
    "SourceIdentifiers": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "applicationId": {
          "type": "integer",
          "minimum": 0
        },
        "categoryId": {
          "type": "integer",
          "minimum": 0
        },
        "counterId": {
          "type": "integer",
          "minimum": 0
        },
        "categoryName": {
          "type": "string"
        },
        "userFriendlyName": {
          "type": "string"
        },
        "oid": {
          "type": "string"
        }
      }
    },
    "DlPrbUtilization": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name",
        "value",
        "unit",
        "quality"
      ],
      "properties": {
        "name": {
          "const": "dlPrbUtilization"
        },
        "value": {
          "type": "number",
          "minimum": 0,
          "maximum": 100
        },
        "unit": {
          "const": "percent"
        },
        "quality": {
          "$ref": "#/$defs/Quality"
        },
        "sourceIdentifiers": {
          "$ref": "#/$defs/SourceIdentifiers"
        }
      }
    },
    "RrcConnectedUeCount": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name",
        "value",
        "unit",
        "quality"
      ],
      "properties": {
        "name": {
          "const": "rrcConnectedUeCount"
        },
        "value": {
          "type": "integer",
          "minimum": 0,
          "maximum": 600
        },
        "unit": {
          "const": "count"
        },
        "quality": {
          "$ref": "#/$defs/Quality"
        },
        "sourceIdentifiers": {
          "$ref": "#/$defs/SourceIdentifiers"
        }
      }
    },
    "TxMutingActivation": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name",
        "value",
        "quality"
      ],
      "properties": {
        "name": {
          "const": "txMutingActivation"
        },
        "value": {
          "type": "string",
          "enum": [
            "MUTING_OFF",
            "MUTING_ON",
            "UNKNOWN"
          ]
        },
        "quality": {
          "$ref": "#/$defs/Quality"
        },
        "sourceIdentifiers": {
          "$ref": "#/$defs/SourceIdentifiers"
        }
      }
    },
    "TxMutingFeatureEnable": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name",
        "value",
        "quality"
      ],
      "properties": {
        "name": {
          "const": "txMutingFeatureEnable"
        },
        "value": {
          "type": "boolean"
        },
        "quality": {
          "$ref": "#/$defs/Quality"
        },
        "sourceIdentifiers": {
          "$ref": "#/$defs/SourceIdentifiers"
        }
      }
    },
    "TxPathOffPattern": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name",
        "value",
        "quality"
      ],
      "properties": {
        "name": {
          "const": "txPathOffPattern"
        },
        "value": {
          "type": "string",
          "enum": [
            "HORIZONTAL_PLANE",
            "VERTICAL_PLANE",
            "UNKNOWN"
          ]
        },
        "quality": {
          "$ref": "#/$defs/Quality"
        },
        "sourceIdentifiers": {
          "$ref": "#/$defs/SourceIdentifiers"
        }
      }
    },
    "MruSynchronizationState": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name",
        "value",
        "quality"
      ],
      "properties": {
        "name": {
          "const": "mruSynchronizationState"
        },
        "value": {
          "type": "string",
          "enum": [
            "SYNCHRONIZED",
            "NOT_SYNCHRONIZED",
            "UNKNOWN"
          ]
        },
        "quality": {
          "$ref": "#/$defs/Quality"
        }
      }
    },
    "BlockingAlarmPresent": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name",
        "value",
        "quality"
      ],
      "properties": {
        "name": {
          "const": "blockingAlarmPresent"
        },
        "value": {
          "type": "boolean"
        },
        "quality": {
          "$ref": "#/$defs/Quality"
        }
      }
    },
    "AlarmReference": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "alarmId",
        "alarmName",
        "severity"
      ],
      "properties": {
        "alarmId": {
          "type": "integer",
          "minimum": 0
        },
        "alarmName": {
          "type": "string"
        },
        "severity": {
          "type": "string",
          "enum": [
            "CRITICAL",
            "MAJOR",
            "MINOR",
            "WARNING",
            "INDETERMINATE",
            "CLEARED"
          ]
        },
        "cnum": {
          "type": "integer",
          "minimum": 0
        }
      }
    }
  }
}
```

## A.4.3 Valid delivery example

```json
{
  "schemaVersion": "1.0.0",
  "dataJobId": "job-ccdu-demo-0001",
  "target": {
    "ranNodeId": "CCDU-001",
    "managedElementId": "CCDU-001",
    "gnbDuFunctionId": "DU-1",
    "cellId": "Cell-101",
    "cnum": 1,
    "distinguishedName": "ME=CCDU-001,GNBDUFunction=DU-1,NRCellDU=Cell-101"
  },
  "measurementTime": "2026-09-30T09:30:00Z",
  "producerTime": "2026-09-30T09:30:02Z",
  "measurements": [
    {
      "name": "dlPrbUtilization",
      "value": 18.4,
      "unit": "percent",
      "quality": "VALID",
      "sourceIdentifiers": {
        "applicationId": 3,
        "categoryId": 20,
        "counterId": 501
      }
    },
    {
      "name": "rrcConnectedUeCount",
      "value": 4,
      "unit": "count",
      "quality": "VALID",
      "sourceIdentifiers": {
        "applicationId": 1,
        "categoryId": 4,
        "counterId": 502
      }
    },
    {
      "name": "txMutingActivation",
      "value": "MUTING_OFF",
      "quality": "VALID",
      "sourceIdentifiers": {
        "categoryName": "DU_CELLVS",
        "userFriendlyName": "MIMOTXMUTING_TXMUTINGACTIVATION",
        "oid": "3.20.[0].15.[0].5000.137.2:TXMUTINGACTIVATION"
      }
    },
    {
      "name": "txMutingFeatureEnable",
      "value": true,
      "quality": "VALID",
      "sourceIdentifiers": {
        "categoryName": "DU_CELLVS",
        "userFriendlyName": "MIMOTXMUTING_TXMUTINGFEATUREENABLE",
        "oid": "3.20.[0].15.[0].5000.137.3:TXMUTINGFEATUREENABLE"
      }
    },
    {
      "name": "txPathOffPattern",
      "value": "HORIZONTAL_PLANE",
      "quality": "VALID",
      "sourceIdentifiers": {
        "categoryName": "DU_CELLVS",
        "userFriendlyName": "MIMOTXMUTING_TXPATHOFFPATTERN",
        "oid": "3.20.[0].15.[0].5000.137.1:TXPATHOFFPATTERN"
      }
    },
    {
      "name": "mruSynchronizationState",
      "value": "SYNCHRONIZED",
      "quality": "VALID"
    },
    {
      "name": "blockingAlarmPresent",
      "value": false,
      "quality": "VALID"
    }
  ],
  "activeBlockingAlarms": []
}
```

## A.4.4 Delivery example with a blocking alarm

```json
{
  "schemaVersion": "1.0.0",
  "dataJobId": "job-ccdu-demo-0002",
  "target": {
    "ranNodeId": "CCDU-001",
    "managedElementId": "CCDU-001",
    "gnbDuFunctionId": "DU-1",
    "cellId": "Cell-101",
    "cnum": 1
  },
  "measurementTime": "2026-09-30T09:35:00Z",
  "measurements": [
    {
      "name": "dlPrbUtilization",
      "value": 16.2,
      "unit": "percent",
      "quality": "VALID"
    },
    {
      "name": "rrcConnectedUeCount",
      "value": 3,
      "unit": "count",
      "quality": "VALID"
    },
    {
      "name": "txMutingActivation",
      "value": "MUTING_OFF",
      "quality": "VALID"
    },
    {
      "name": "txMutingFeatureEnable",
      "value": true,
      "quality": "VALID"
    },
    {
      "name": "txPathOffPattern",
      "value": "HORIZONTAL_PLANE",
      "quality": "VALID"
    },
    {
      "name": "mruSynchronizationState",
      "value": "SYNCHRONIZED",
      "quality": "VALID"
    },
    {
      "name": "blockingAlarmPresent",
      "value": true,
      "quality": "VALID"
    }
  ],
  "activeBlockingAlarms": [
    {
      "alarmId": 13325,
      "alarmName": "L1_FH_COMM_DOWN_ERROR",
      "severity": "CRITICAL",
      "cnum": 1
    }
  ]
}
```

---

# A.5 `JIO_CCDU_EnergySavingThresholds.schema.json`

## A.5.1 Purpose

This schema defines the instantaneous decision thresholds and safety policy used by the rApp.

## A.5.2 Complete schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.jio.com/ccdu/energy-saving/thresholds/1.0.0",
  "title": "CCDU Energy-Saving Threshold Configuration",
  "description": "Configuration for instantaneous threshold-based CCDU TX muting decisions.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "activation",
    "deactivation",
    "requestedConfiguration",
    "measurementPolicy",
    "alarmPolicy",
    "executionPolicy"
  ],
  "properties": {
    "activation": {
      "$ref": "#/$defs/ActivationThresholds"
    },
    "deactivation": {
      "$ref": "#/$defs/DeactivationThresholds"
    },
    "requestedConfiguration": {
      "$ref": "#/$defs/RequestedConfiguration"
    },
    "measurementPolicy": {
      "$ref": "#/$defs/MeasurementPolicy"
    },
    "alarmPolicy": {
      "$ref": "#/$defs/AlarmPolicy"
    },
    "executionPolicy": {
      "$ref": "#/$defs/ExecutionPolicy"
    }
  },
  "$defs": {
    "ActivationThresholds": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "prbUtilizationPercent",
        "rrcConnectedUeCount",
        "combinationOperator"
      ],
      "properties": {
        "prbUtilizationPercent": {
          "type": "number",
          "minimum": 0,
          "maximum": 100,
          "default": 40
        },
        "rrcConnectedUeCount": {
          "type": "integer",
          "minimum": 0,
          "maximum": 600,
          "default": 10
        },
        "combinationOperator": {
          "type": "string",
          "const": "AND"
        }
      }
    },
    "DeactivationThresholds": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "prbUtilizationPercent",
        "rrcConnectedUeCount",
        "combinationOperator"
      ],
      "properties": {
        "prbUtilizationPercent": {
          "type": "number",
          "minimum": 0,
          "maximum": 100,
          "default": 42
        },
        "rrcConnectedUeCount": {
          "type": "integer",
          "minimum": 0,
          "maximum": 600,
          "default": 12
        },
        "combinationOperator": {
          "type": "string",
          "const": "OR"
        }
      }
    },
    "RequestedConfiguration": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "txPathOffPattern",
        "requireFeatureEnabled",
        "reducedTxYangValue",
        "fullTxYangValue"
      ],
      "properties": {
        "txPathOffPattern": {
          "type": "string",
          "enum": [
            "HORIZONTAL_PLANE",
            "VERTICAL_PLANE"
          ],
          "default": "HORIZONTAL_PLANE"
        },
        "requireFeatureEnabled": {
          "type": "boolean",
          "const": true
        },
        "reducedTxYangValue": {
          "type": "string",
          "const": "MUTING_ON"
        },
        "fullTxYangValue": {
          "type": "string",
          "const": "MUTING_OFF"
        }
      }
    },
    "MeasurementPolicy": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "maximumSampleAgeSeconds",
        "requiredQuality",
        "missingMeasurementAction",
        "staleMeasurementAction"
      ],
      "properties": {
        "maximumSampleAgeSeconds": {
          "type": "integer",
          "minimum": 1,
          "maximum": 3600,
          "default": 900
        },
        "requiredQuality": {
          "type": "string",
          "const": "VALID"
        },
        "missingMeasurementAction": {
          "type": "string",
          "enum": [
            "NO_ACTION",
            "REQUEST_FULL_TX"
          ],
          "default": "NO_ACTION"
        },
        "staleMeasurementAction": {
          "type": "string",
          "enum": [
            "NO_ACTION",
            "REQUEST_FULL_TX"
          ],
          "default": "NO_ACTION"
        }
      }
    },
    "AlarmPolicy": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "blockReducedTxOnActiveAlarm",
        "requestFullTxOnBlockingAlarm",
        "blockingAlarmIds",
        "triggerAlarmIds"
      ],
      "properties": {
        "blockReducedTxOnActiveAlarm": {
          "type": "boolean",
          "const": true
        },
        "requestFullTxOnBlockingAlarm": {
          "type": "boolean",
          "default": true
        },
        "blockingAlarmIds": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "type": "integer",
            "minimum": 0
          }
        },
        "triggerAlarmIds": {
          "type": "array",
          "uniqueItems": true,
          "items": {
            "type": "integer",
            "minimum": 0
          }
        }
      }
    },
    "ExecutionPolicy": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "verifyWithReadback",
        "operationTimeoutSeconds",
        "maximumRetries",
        "rollbackOnVerificationFailure"
      ],
      "properties": {
        "verifyWithReadback": {
          "type": "boolean",
          "const": true
        },
        "operationTimeoutSeconds": {
          "type": "integer",
          "minimum": 1,
          "maximum": 120,
          "default": 15
        },
        "maximumRetries": {
          "type": "integer",
          "minimum": 0,
          "maximum": 3,
          "default": 1
        },
        "rollbackOnVerificationFailure": {
          "type": "boolean",
          "default": true
        }
      }
    }
  }
}
```

## A.5.3 Example threshold configuration

The pilot's `thresholds.json` is this example with `missingMeasurementAction` and `staleMeasurementAction` set to `REQUEST_FULL_TX` (§6.2).

```json
{
  "activation": {
    "prbUtilizationPercent": 40.0,
    "rrcConnectedUeCount": 10,
    "combinationOperator": "AND"
  },
  "deactivation": {
    "prbUtilizationPercent": 42.0,
    "rrcConnectedUeCount": 12,
    "combinationOperator": "OR"
  },
  "requestedConfiguration": {
    "txPathOffPattern": "HORIZONTAL_PLANE",
    "requireFeatureEnabled": true,
    "reducedTxYangValue": "MUTING_ON",
    "fullTxYangValue": "MUTING_OFF"
  },
  "measurementPolicy": {
    "maximumSampleAgeSeconds": 900,
    "requiredQuality": "VALID",
    "missingMeasurementAction": "NO_ACTION",
    "staleMeasurementAction": "NO_ACTION"
  },
  "alarmPolicy": {
    "blockReducedTxOnActiveAlarm": true,
    "requestFullTxOnBlockingAlarm": true,
    "blockingAlarmIds": [
      13313,
      13314,
      13319,
      13322,
      13323,
      14081,
      13325,
      5124,
      4360,
      4609
    ],
    "triggerAlarmIds": [
      13321,
      5121,
      5123
    ]
  },
  "executionPolicy": {
    "verifyWithReadback": true,
    "operationTimeoutSeconds": 15,
    "maximumRetries": 1,
    "rollbackOnVerificationFailure": true
  }
}
```

## A.5.4 Hysteresis validation

The configuration loader shall verify:

```text
activation.prbUtilizationPercent
    <
deactivation.prbUtilizationPercent
```

and:

```text
activation.rrcConnectedUeCount
    <
deactivation.rrcConnectedUeCount
```

For the example:

```text
40 < 42

10 < 12
```

---

# A.6 `JIO_CCDU_EnergySaving_OAuth_Profile.yaml`

## A.6.1 Purpose

This profile defines the deployment-specific OAuth client, scopes, resources, permissions, and safety policies for the Energy-Saving rApp.

It configures behavior on top of:

```text
IETF RFC 6749
IETF RFC 6750
```

In ai-ran-ref, token validity is enforced by R1 Termination and SME. The resource, attribute and value restrictions below map to TS 28.319 MSAC rules in `ran-nf-oam` (`msac.py`) and to the rApp safeguards (rate, blast-radius and magnitude limits). The pilot does not configure them.

## A.6.2 Complete example

```yaml
apiVersion: security.jio.com/v1
kind: OAuthClientProfile

metadata:
  name: ccdu-energy-saving-rapp
  version: 1.0.0
  description: >-
    OAuth client and authorization profile for the CCDU
    instantaneous threshold-based Energy-Saving rApp.

client:
  clientId: ccdu-energy-saving-rapp

  clientAuthenticationMethods:
    - tls_client_auth
    - client_secret_basic

  grantTypes:
    - client_credentials

  tokenEndpointAuthSigningAlg: RS256

  certificateIdentity:
    subjectDistinguishedName: >-
      CN=ccdu-energy-saving-rapp,
      OU=Non-RT-RIC,
      O=Jio

token:
  tokenType: Bearer
  accessTokenLifetimeSeconds: 3600
  refreshTokenEnabled: false

  requiredClaims:
    - iss
    - sub
    - aud
    - iat
    - exp
    - jti
    - scope

  audience:
    - r1-service-management
    - r1-data-management
    - r1-configuration-management
    - r1-fault-management

scopes:
  - name: r1.service.discover
    description: Discover R1 service APIs.

  - name: r1.data.read
    description: >-
      Discover DME types, create ONE_TIME data jobs,
      and receive instantaneous CCDU data.

  - name: r1.cm.read
    description: Read CCDU TX muting configuration.

  - name: r1.cm.write
    description: >-
      Request MUTING_ON or MUTING_OFF for authorized cells.

  - name: r1.fm.read
    description: Read alarms used by the energy-saving safety policy.

resourceAuthorization:
  managedElements:
    - managedElementId: CCDU-001

      managedFunctions:
        - functionType: GNBDUFunction
          functionId: DU-1

      managedObjects:
        - objectClass: NRCellDU
          objectIds:
            - Cell-101

      allowedR1Operations:
        - SERVICE_DISCOVERY
        - DATA_JOB_CREATE
        - DATA_JOB_READ
        - CONFIGURATION_READ
        - CONFIGURATION_PATCH
        - ALARM_READ

      allowedAttributes:
        - txMutingFeatureEnable
        - txPathOffPattern
        - txMutingActivation

      allowedAttributeValues:
        txMutingFeatureEnable:
          - true

        txPathOffPattern:
          - HORIZONTAL_PLANE
          - VERTICAL_PLANE

        txMutingActivation:
          - MUTING_OFF
          - MUTING_ON

policy:
  requireFreshMeasurements: true
  maximumSampleAgeSeconds: 900

  requireValidMeasurementQuality: true

  requireFeatureEnabled: true

  blockReducedTxOnActiveCriticalAlarm: true

  blockReducedTxWhenMruNotSynchronized: true

  requireReadbackVerification: true

  permitFullTxFailSafeRequest: true

  denyUnknownManagedElements: true
  denyUnknownCells: true
  denyUnknownAttributes: true

audit:
  enabled: true

  requiredFields:
    - timestamp
    - clientId
    - subject
    - managedElementId
    - managedObjectClass
    - managedObjectId
    - operation
    - decisionId
    - requestedValues
    - appliedValues
    - result
    - failureReason
    - correlationId
    - oidOperationId
    - readbackOperationId

problemDetails:
  namespace: urn:jio:ccdu:problem

  supportedProblemCodes:
    - invalid-measurement-quality
    - stale-measurement
    - missing-measurement
    - feature-disabled
    - mru-not-synchronized
    - critical-alarm-present
    - configuration-not-authorized
    - tx-muting-rejected
    - state-verification-failed
    - oid-timeout
    - oid-invalid-response
```

## A.6.3 Example access-token claims

```json
{
  "iss": "https://auth.smo.example.com",
  "sub": "ccdu-energy-saving-rapp",
  "aud": [
    "r1-data-management",
    "r1-configuration-management",
    "r1-fault-management"
  ],
  "iat": 1790758800,
  "exp": 1790762400,
  "jti": "token-ccdu-es-0001",
  "scope": "r1.service.discover r1.data.read r1.cm.read r1.cm.write r1.fm.read",
  "authorizedRanNodes": [
    "CCDU-001"
  ],
  "authorizedManagedObjects": [
    "GNBDUFunction=DU-1,NRCellDU=Cell-101"
  ],
  "authorizedAttributes": [
    "txMutingFeatureEnable",
    "txPathOffPattern",
    "txMutingActivation"
  ]
}
```

---

# A.7 Example DME-Type Registration

The vendor production and delivery schemas are registered using the standard R1 `DmeTypeRelatedCapabilities` structure. In ai-ran-ref the equivalent call is `POST /dme/production-capabilities` (`namespace`, `name`, `version`, `typeName`, `producerId`, `dataProductionSchema`, callback URLs); the pilot uses the per-counter types RAN NF OAM registers instead (§10.5).

```json
{
  "dmeTypeDefinition": {
    "dmeTypeId": {
      "namespace": "urn:jio:ccdu:energy-saving",
      "name": "tx-muting-instantaneous-input",
      "version": "1.0.0"
    },
    "metadata": {
      "dataCategory": [
        "PM_COUNTERS",
        "CONFIGURATION_STATE",
        "FAULT_STATE"
      ],
      "rat": [
        "5G"
      ]
    },
    "dataProductionSchema": {
      "$id": "https://schemas.jio.com/ccdu/energy-saving/instantaneous-job/1.0.0",
      "$ref": "JIO_CCDU_InstantaneousJob.schema.json"
    },
    "dataDeliverySchemas": [
      {
        "type": "JSON_SCHEMA",
        "deliverySchemaId": "ccdu-instantaneous-values-v1",
        "schema": "JIO_CCDU_InstantaneousValues.schema.json"
      }
    ],
    "dataDeliveryMechanisms": [
      {
        "dataDeliveryMethod": "PUSH_HTTP"
      }
    ]
  },
  "dataAccessEndpoint": {
    "ipv4Addr": "10.20.0.20",
    "port": 443,
    "securityMethods": [
      "OAUTH"
    ]
  },
  "dataDeliveryMode": [
    "ONE_TIME"
  ],
  "constraints": {
    "supportedSampleSelections": [
      "LATEST_AVAILABLE"
    ],
    "maximumSampleAgeSeconds": 900,
    "supportedManagedElements": [
      "CCDU-001"
    ],
    "supportedManagedObjectClasses": [
      "NRCellDU"
    ]
  }
}
```

---

# A.8 Example rApp Decision Record

This object is internal to the rApp. It is not a standardized R1 protocol object. The pilot keeps one per pass in its state file (`decisions`).

```json
{
  "decisionId": "ES-DEMO-0001",
  "decisionTime": "2026-09-30T09:30:03Z",
  "target": {
    "ranNodeId": "CCDU-001",
    "managedElementId": "CCDU-001",
    "gnbDuFunctionId": "DU-1",
    "cellId": "Cell-101",
    "cnum": 1
  },
  "currentState": {
    "txMutingFeatureEnable": true,
    "txMutingActivation": "MUTING_OFF",
    "txPathOffPattern": "HORIZONTAL_PLANE"
  },
  "instantaneousValues": {
    "dlPrbUtilization": 18.4,
    "rrcConnectedUeCount": 4,
    "mruSynchronizationState": "SYNCHRONIZED",
    "blockingAlarmPresent": false
  },
  "thresholds": {
    "prbActivationThreshold": 40.0,
    "rrcUeActivationThreshold": 10,
    "prbDeactivationThreshold": 42.0,
    "rrcUeDeactivationThreshold": 12
  },
  "evaluation": {
    "prbBelowActivationThreshold": true,
    "ueCountBelowActivationThreshold": true,
    "featureEnabled": true,
    "mruSynchronized": true,
    "blockingAlarmAbsent": true,
    "allMeasurementsValid": true,
    "allMeasurementsFresh": true
  },
  "decision": {
    "recommendedTxMode": "REDUCED_TX",
    "yangActivationValue": "MUTING_ON",
    "yangPathOffPattern": "HORIZONTAL_PLANE",
    "reasonCode": "INSTANTANEOUS_LOW_LOAD"
  }
}
```

---

# A.9 Example O1 Configuration Payloads

These payloads use the existing leaves from:

```text
gnb_du_vs_tx_muting.yang
```

## A.9.1 Enable TX muting

```json
{
  "gnb_du_vs_tx_muting:mimoTxMuting": {
    "txMutingFeatureEnable": true,
    "txPathOffPattern": "HORIZONTAL_PLANE",
    "txMutingActivation": "MUTING_ON"
  }
}
```

## A.9.2 Disable TX muting

```json
{
  "gnb_du_vs_tx_muting:mimoTxMuting": {
    "txMutingActivation": "MUTING_OFF"
  }
}
```

## A.9.3 Readback response

```json
{
  "gnb_du_vs_tx_muting:mimoTxMuting": {
    "txMutingFeatureEnable": true,
    "txPathOffPattern": "HORIZONTAL_PLANE",
    "txMutingActivation": "MUTING_ON"
  }
}
```

## A.9.4 NETCONF `edit-config` for a real CCDU

A.9.1 as an RFC 6241 `edit-config` against the vendor subtree.

```xml
<rpc message-id="0x02000001" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <edit-config>
    <target><running/></target>
    <default-operation>merge</default-operation>
    <config>
      <mimoTxMuting xmlns="urn:rdns:com:radisys:nr:gnbduVsTxMuting">
        <txMutingFeatureEnable>true</txMutingFeatureEnable>
        <txPathOffPattern>HORIZONTAL_PLANE</txPathOffPattern>
        <txMutingActivation>MUTING_ON</txMutingActivation>
      </mimoTxMuting>
    </config>
  </edit-config>
</rpc>
```

The `mimoTxMuting` element sits under the `ManagedElement/GNBDUFunction/NRCellDU/gnbCellDuVsCfg` path. Ancestor elements are omitted here for brevity. In the pilot, RAN NF OAM sends the three leaves on a `managed-object` element (`ref="ccdu-001"`, `function-ref="NRCellDU=101"`) to `mock-o1-adaptor`.

## A.9.5 DME `/actions` request from the rApp

The request the pilot sends (`pilot.py` `_action()`):

```json
{
  "actionId": "3f0c9a4e-6a52-4f55-9f1e-2b8a1f6c0d11",
  "requestedBy": "ccdu-tx-muting-pilot",
  "scope": "single-ME",
  "changes": [
    {
      "managedElementRef": "ccdu-001",
      "className": "NRCellDU",
      "managedFunctionRef": "NRCellDU=101",
      "attributeChanges": {
        "txMutingFeatureEnable": "true",
        "txPathOffPattern": "HORIZONTAL_PLANE",
        "txMutingActivation": "MUTING_ON"
      }
    }
  ],
  "sourceContext": {
    "rApp": "ccdu-tx-muting-pilot",
    "decisionId": "ES-PILOT-0001",
    "reason": "INSTANTANEOUS_LOW_LOAD",
    "dataJobIds": ["<DL_PRB_UTILIZATION job>", "<RRC_CONNECTED_UE job>", "<MRU_SYNC_STATE job>"]
  }
}
```

---

# A.10 Example Problem Details

The response envelopes follow the HTTP Problem Details format. The CCDU problem types are vendor-defined.

## A.10.1 Blocking alarm present

```json
{
  "type": "urn:jio:ccdu:problem:critical-alarm-present",
  "title": "Blocking alarm is active",
  "status": 409,
  "detail": "MUTING_ON cannot be requested while a blocking alarm is active for Cell-101.",
  "instance": "ES-DEMO-0001",
  "activeAlarms": [
    {
      "alarmId": 13325,
      "alarmName": "L1_FH_COMM_DOWN_ERROR",
      "severity": "CRITICAL",
      "cnum": 1
    }
  ]
}
```

## A.10.2 Stale measurement

```json
{
  "type": "urn:jio:ccdu:problem:stale-measurement",
  "title": "Instantaneous measurement is stale",
  "status": 409,
  "detail": "The measurement exceeds the configured maximum sample age of 900 seconds.",
  "instance": "ES-DEMO-0001",
  "measurementTime": "2026-09-30T09:00:00Z",
  "evaluationTime": "2026-09-30T09:30:03Z"
}
```

## A.10.3 Feature disabled

```json
{
  "type": "urn:jio:ccdu:problem:feature-disabled",
  "title": "TX muting feature is disabled",
  "status": 409,
  "detail": "MUTING_ON cannot be requested while txMutingFeatureEnable is false.",
  "instance": "ES-DEMO-0001",
  "txMutingFeatureEnable": false
}
```

## A.10.4 OID timeout

```json
{
  "type": "urn:jio:ccdu:problem:oid-timeout",
  "title": "CCDU OID operation timed out",
  "status": 504,
  "detail": "No valid OID response was received before the configured timeout.",
  "instance": "ES-DEMO-0001",
  "oidOperationId": "0x02000001",
  "timeoutSeconds": 15,
  "retryCount": 1
}
```

## A.10.5 State-verification failure

```json
{
  "type": "urn:jio:ccdu:problem:state-verification-failed",
  "title": "TX muting state verification failed",
  "status": 409,
  "detail": "The OID request was accepted, but readback returned MUTING_OFF.",
  "instance": "ES-DEMO-0001",
  "requestedState": "MUTING_ON",
  "appliedState": "MUTING_OFF",
  "oidOperationId": "0x02000001",
  "readbackOperationId": "0x02000002"
}
```

---

# A.11 Recommended Artifact Directory

```text
energy-saving-rapp/
├── schemas/
│   ├── JIO_CCDU_InstantaneousJob.schema.json
│   ├── JIO_CCDU_InstantaneousValues.schema.json
│   └── JIO_CCDU_EnergySavingThresholds.schema.json
│
├── security/
│   └── JIO_CCDU_EnergySaving_OAuth_Profile.yaml
│
├── examples/
│   ├── data-job-request.json
│   ├── instantaneous-values-success.json
│   ├── instantaneous-values-blocked.json
│   ├── decision-reduced-tx.json
│   ├── decision-full-tx.json
│   ├── decision-no-change.json
│   ├── o1-enable-tx-muting.json
│   ├── o1-disable-tx-muting.json
│   ├── problem-stale-measurement.json
│   ├── problem-critical-alarm-present.json
│   ├── problem-oid-timeout.json
│   └── problem-state-verification-failed.json
│
└── mappings/
    ├── yang-to-oid-mapping.csv
    ├── pm-counter-mapping.csv
    └── alarm-policy-mapping.csv
```

---

# A.12 Example `yang-to-oid-mapping.csv`

```csv
yang-module,yang-container,yang-leaf,yang-value,category,user-friendly-name,oid,level,access
gnb_du_vs_tx_muting,mimoTxMuting,txPathOffPattern,HORIZONTAL_PLANE,DU_CELLVS,MIMOTXMUTING_TXPATHOFFPATTERN,3.20.[0].15.[0].5000.137.1:TXPATHOFFPATTERN,CELLDU,rw
gnb_du_vs_tx_muting,mimoTxMuting,txPathOffPattern,VERTICAL_PLANE,DU_CELLVS,MIMOTXMUTING_TXPATHOFFPATTERN,3.20.[0].15.[0].5000.137.1:TXPATHOFFPATTERN,CELLDU,rw
gnb_du_vs_tx_muting,mimoTxMuting,txMutingActivation,MUTING_OFF,DU_CELLVS,MIMOTXMUTING_TXMUTINGACTIVATION,3.20.[0].15.[0].5000.137.2:TXMUTINGACTIVATION,CELLDU,rw
gnb_du_vs_tx_muting,mimoTxMuting,txMutingActivation,MUTING_ON,DU_CELLVS,MIMOTXMUTING_TXMUTINGACTIVATION,3.20.[0].15.[0].5000.137.2:TXMUTINGACTIVATION,CELLDU,rw
gnb_du_vs_tx_muting,mimoTxMuting,txMutingFeatureEnable,false,DU_CELLVS,MIMOTXMUTING_TXMUTINGFEATUREENABLE,3.20.[0].15.[0].5000.137.3:TXMUTINGFEATUREENABLE,CELLDU,rw
gnb_du_vs_tx_muting,mimoTxMuting,txMutingFeatureEnable,true,DU_CELLVS,MIMOTXMUTING_TXMUTINGFEATUREENABLE,3.20.[0].15.[0].5000.137.3:TXMUTINGFEATUREENABLE,CELLDU,rw
```

---

# A.13 Example `pm-counter-mapping.csv`

The PRB and UE counter identifiers below remain placeholders until confirmed from the approved CCDU PM catalogue. The `ai-ran-ref-counter-type` column is the RAN NF OAM PM counter type the pilot uses.

```csv
dme-measurement-name,ai-ran-ref-counter-type,unit,application-id,category-id,counter-id,cnum-applicable,quality-rule,status
dlPrbUtilization,DL_PRB_UTILIZATION,percent,3,TBD,TBD,true,VALID when current sample is available,TBD
rrcConnectedUeCount,RRC_CONNECTED_UE,count,TBD,TBD,TBD,true,VALID when current sample is available,TBD
txMutingActivation,-,enum,4,DU_CELLVS,137.2,true,VALID after successful YANG or OID read,CONFIRMED
txMutingFeatureEnable,-,boolean,4,DU_CELLVS,137.3,true,VALID after successful YANG or OID read,CONFIRMED
txPathOffPattern,-,enum,4,DU_CELLVS,137.1,true,VALID after successful YANG or OID read,CONFIRMED
mruSynchronizationState,MRU_SYNC_STATE,enum,TBD,TBD,TBD,TBD,VALID when synchronization source is current,TBD
blockingAlarmPresent,-,boolean,derived,derived,derived,true,VALID when FM snapshot is current,DERIVED
```

---

# A.14 Example `alarm-policy-mapping.csv`

```csv
alarm-id,alarm-name,severity,policy-role,block-muting-on,request-muting-off,scope
13313,SYS_ALARM_CELL_ADMIN_LOCKED,CRITICAL,BLOCKING,true,true,NRCellDU
13314,SW_ALARM_CELL_SETUP_FAIL,MAJOR,BLOCKING,true,true,NRCellDU
13319,CELL_ALARM_CELL_CONFIG_FAIL,MAJOR,BLOCKING,true,true,NRCellDU
13322,COM_ALARM_CELL_TDD_L1_COMM_FAIL,CRITICAL,BLOCKING,true,true,DU
13323,COM_ALARM_CELL_FDD_L1_COMM_FAIL,CRITICAL,BLOCKING,true,true,DU
14081,COM_ALARM_F1_C_COMM_DOWN,CRITICAL,BLOCKING,true,true,DU
13325,L1_FH_COMM_DOWN_ERROR,CRITICAL,BLOCKING,true,true,NRCellDU
5124,SW_ALARM_F1_SETUP_FAILURE,CRITICAL,BLOCKING,true,true,CUCP
4360,SW_ALARM_CU_CONFIG_FAIL,CRITICAL,BLOCKING,true,true,CUCP
4609,COM_ALARM_ALL_AMF_COMM_DOWN,CRITICAL,BLOCKING,true,true,CUCP
13321,SYS_ALARM_CELL_DL_PRB_UTILIZATION_LOW,MINOR,TRIGGER,false,false,NRCellDU
5121,SW_ALARM_SLEEPING_CELL_DETECTION,MAJOR,TRIGGER,false,false,NRCellDU
5123,SW_ALARM_MICRO_SLEEPING_CELL_DETECTION,MINOR,TRIGGER,false,false,NRCellDU
```

---

# A.15 Appendix Artifact Summary

```text
Standard R1 DataJobInfo
    +
JIO_CCDU_InstantaneousJob.schema.json
    =
Complete one-time CCDU data-job request
```

```text
Standard R1 HTTP Push Data procedure
    +
JIO_CCDU_InstantaneousValues.schema.json
    =
Complete one-time CCDU measurement delivery
```

```text
Instantaneous measurement delivery
    +
JIO_CCDU_EnergySavingThresholds.schema.json
    =
REDUCED_TX, FULL_TX, or NO_CHANGE decision
```

```text
OAuth 2.0
    +
JIO_CCDU_EnergySaving_OAuth_Profile.yaml
    =
Deployment-specific rApp authorization
```

```text
DME /actions -> RAN NF OAM config job
    +
gnb_du_vs_tx_muting.yang
    =
CCDU TX muting configuration operation
```

---

# A.16 Implementation Notes

1. The existing `gnb_du_vs_tx_muting.yang` module is the authoritative TX muting YANG model.

2. A second TX muting YANG module shall not be created.

3. The vendor job schema defines only `DataJobInfo.productionJobDefinition`.

4. The vendor delivery schema defines only the DME-specific delivery payload.

5. The standard `DataJobInfo` envelope shall not be redefined.

6. The standard `DmeTypeRelatedCapabilities` envelope shall not be redefined.

7. OAuth profile content is deployment configuration, not a replacement for OAuth standards.

8. The threshold schema defines local rApp logic and is not an O-RAN protocol schema.

9. PM counter identifiers remain placeholders until confirmed from the approved CCDU PM catalogue.

10. The current alarm IDs are taken from the supplied DU and CU alarm catalogues.

11. `txMutingActivation`, `txMutingFeatureEnable`, and `txPathOffPattern` map directly to the existing vendor YANG model.

12. Every `MUTING_ON` or `MUTING_OFF` request shall be followed by readback.

13. `NO_CHANGE` shall not generate a state-changing O1 operation.

14. A blocking alarm shall prevent a new `MUTING_ON` request.

15. A stale, missing, invalid, or estimated mandatory measurement shall prevent a new `MUTING_ON` request unless an explicit policy permits estimated data.

16. When muting is already active, a blocking alarm or unsafe state should request or locally enforce `MUTING_OFF`.

17. The rApp shall not receive or depend on CNUM, Category, UFN, or OID operation identifiers for its decision logic.

18. CCDU source identifiers may be included only for troubleshooting and audit purposes.

19. All artifacts shall use explicit semantic versions.

20. Schema changes that break compatibility shall increment the major version.

21. Additional optional fields shall increment the minor version.

22. Editorial fixes shall increment the patch version.

23. A production rApp shall call the SMO through R1 Termination with an SME-issued Bearer token. The pilot calls services directly inside the compose network (§10.5).

24. Configuration changes shall be requested through DME `/actions`. DME forwards them to RAN NF OAM `POST /config-jobs`. The rApp shall not call O1 directly.

25. RAN NF OAM is the only SMO O1 MnS Consumer. It dispatches CM writes over NETCONF (http-mock, SSH or TLS transport) or RESTCONF, as the element was registered. The pilot uses NETCONF over the http-mock transport to `mock-o1-adaptor`.

26. The CCDU O1 Adaptor shall self-register through `POST /o1-adaptor-endpoints` and send heartbeats. CM writes are dispatched only to an `ACTIVE` endpoint.

27. PM and FM data reaches the rApp through DME in the target design. RAN NF OAM is the DME producer for the PM counter types and `RAN.FaultRecords`. The pilot reads PM from DME and alarms from RAN NF OAM.

28. The `r1.cm.read` and `r1.cm.write` scopes in A.6 map to DME action mediation and RAN NF OAM configuration read in ai-ran-ref. There is no standalone R1 CM service.

---
