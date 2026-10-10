# Call Flow: Software Management Job — DOWNLOAD → INSTALL → ACTIVATE (and a Failed Phase)

A RAN NF OAM `SoftwareManagementJob` driven through O-RAN WG4 Software Management's
`o-ran-software-management.yang` RPC set (`software-download`/`software-install`/
`software-activate`) and their three completion notifications
(`download-event`/`install-event`/`activation-event`), including a failed phase
(HISTORY.md SA-O1-3).

```mermaid
sequenceDiagram
    actor Operator
    participant NFOAM as RAN NF OAM

    rect rgb(240, 255, 240)
    Note over Operator,NFOAM: Happy path — three phases, three advance calls
    Operator->>NFOAM: POST /software-management-jobs (managedElementRef, ruInstanceId?)
    NFOAM->>NFOAM: SwmState: PENDING -START-> IN_PROGRESS (synchronous, same<br/>elision pattern as every other Phase-1 job creation in this build)
    NFOAM-->>Operator: jobId, status=IN_PROGRESS, phase=DOWNLOAD

    Operator->>NFOAM: POST /software-management-jobs/{id}/advance (succeeded=true)
    NFOAM->>NFOAM: phase=DOWNLOAD -> event=DOWNLOAD_OK -> status stays IN_PROGRESS, phase->INSTALL
    NFOAM-->>Operator: jobId, status=IN_PROGRESS, phase=INSTALL

    Operator->>NFOAM: POST /software-management-jobs/{id}/advance (succeeded=true)
    NFOAM->>NFOAM: phase=INSTALL -> event=INSTALL_OK -> status stays IN_PROGRESS, phase->ACTIVATE
    NFOAM-->>Operator: jobId, status=IN_PROGRESS, phase=ACTIVATE

    Operator->>NFOAM: POST /software-management-jobs/{id}/advance (succeeded=true)
    NFOAM->>NFOAM: phase=ACTIVATE -> event=ACTIVATE_OK -> status=COMPLETED (terminal — phase stays ACTIVATE — not in PHASE_ORDER, nothing left to advance to)
    NFOAM-->>Operator: jobId, status=COMPLETED, phase=ACTIVATE
    end

    rect rgb(255, 240, 240)
    Note over Operator,NFOAM: Failure at any phase — one shared terminal state, not a phase-specific one
    Operator->>NFOAM: POST /software-management-jobs (managedElementRef, ruInstanceId?)
    NFOAM-->>Operator: jobId2, status=IN_PROGRESS, phase=DOWNLOAD
    Operator->>NFOAM: POST /software-management-jobs/{id2}/advance (succeeded=true)
    NFOAM-->>Operator: jobId2, status=IN_PROGRESS, phase=INSTALL
    Operator->>NFOAM: POST /software-management-jobs/{id2}/advance (succeeded=false)
    NFOAM->>NFOAM: event=PHASE_FAILED (fires regardless of which phase this job was in) -> status=FAILED
    NFOAM-->>Operator: jobId2, status=FAILED, phase=INSTALL (frozen at the phase that failed)
    Note over Operator,NFOAM: FAILED is terminal — there is no retry/resume transition —<br/>a failed job is re-attempted as a brand-new POST /software-management-jobs,<br/>same "no lightweight update path" principle call flow 02's own<br/>retraining re-entry and call flow 07's own RECOVER re-entry both follow
    end
```

**Key decisions this flow depends on:**
- `phase` and `status` are two separate fields tracking two different things: `status` (`PENDING`/`IN_PROGRESS`/`COMPLETED`/`FAILED`) is the job's own overall progress; `phase` (`DOWNLOAD`/`INSTALL`/`ACTIVATE`) is which of the 3 RPCs it's currently at. A `FAILED` job's `phase` stays frozen at whichever phase actually failed — useful for an operator diagnosing *what* broke, not just *that* something did.
- `PHASE_FAILED` is one shared event regardless of which phase is in progress — `advance_software_job` looks up the current-phase-to-next-event mapping only on the success path (`{"DOWNLOAD": DOWNLOAD_OK, "INSTALL": INSTALL_OK, "ACTIVATE": ACTIVATE_OK}[job.phase]`), and fires the same `PHASE_FAILED` transition on failure — there's no `DOWNLOAD_FAILED`/`INSTALL_FAILED`/`ACTIVATE_FAILED` distinction at the state-machine level, only in the frozen `phase` field's value.
- The 3-phase FSM goes beyond the bare O-RAN WG4 RPCs: the MnS hierarchy workbook flags the base RPCs as missing a `ru-instance-id` (or equivalent) parameter "before reuse at an aggregated/RAN-node level", and `SoftwareManagementJob.ru_instance_id` carries it as a first-class field.
- `PENDING` is real but never observable from outside — `software_update` fires `START` synchronously within the same request that creates the job, so no caller ever sees a `SoftwareManagementJob` in `PENDING` via a `GET`, the same "real state exists, real dispatch elided synchronously" pattern NFO's `Instantiate`/`Scale` (call flow 15) use.
- `software_update` is gated by the axis-2 presence guard: when the ME's vendor has a registered capability, it must implement SWM (`O1_SERVICE_NOT_SUPPORTED` otherwise; call flow 21).
- A job can also belong to a software campaign (`PR-MGT-15`, `ran-nf-oam/README.md`): `POST /software-campaigns` starts one job per element of a wave with `lifecycle.start_software_job` (the function `software_update` uses too), and each `advance` of such a job tells its campaign, which runs its health gate when the whole wave has ended and starts the next wave, halts, or starts one revert job per completed job (`rollback_of`) on a rollback. A campaign made with `jobTimeoutSeconds` (`MGT-15.7`) also has its sweep (`advance-due`, the worker) fail a job still `IN_PROGRESS` when its wave, or its rollback step, is older than that: the job goes `IN_PROGRESS` -> `FAILED` through the same `PHASE_FAILED` event with its `phase` frozen, the campaign logs `JOB_TIMED_OUT` and decides as for any failed job, and a report that arrives later is refused (`advance` of an ended job is 409 `LIFECYCLE_ILLEGAL_TRANSITION`, for every job). With `rollbackOrder` `reverse` the revert jobs of a rollback start wave by wave, the last first. A halted campaign (a failed gate or an operator's halt), a failed rollback and a failed onboarding are announced to the subscribers of `/lifecycle-subscriptions` through the outbox (`docs/NOTIFICATIONS.md`). A job started with `POST /software-management-jobs` has no campaign and behaves as described here.
