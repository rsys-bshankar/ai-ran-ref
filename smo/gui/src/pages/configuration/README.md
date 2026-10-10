# Configuration

Route: `/configuration` (tabs in the hash: `#jobs`, `#new`, `#vendors`, `#trust`, `#onboarding`; `?job=<id>`, `?status=<state|all>`)    Design: handoff `Configuration.dc.html` (BRIEF §4e features 3 and 11)

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| configuration.tiles | sections/ConfigTiles.tsx | halted, running, completed, partial/failed, all (tiles filter the list) | `/api/summary/configuration` (`configJobs.*`) | 15 s | 1 call (shared with the tab count and the list default) |
| configuration.jobs | sections/JobList.tsx | config jobs, server-paged; halted first (default filter while the summary counts any) | `/ran-nf-oam/config-jobs?status=&limit=&offset=` | 15 s | 1 call/page |
| configuration.job | sections/StagedJob.tsx (+ KpiGuard.tsx, JobRollback.tsx) | waves (currentWave of waveCount, each wave's outcome), pause countdown, haltedReason, Continue now / Halt / Abort, Roll back (preview first), KPI guard and result, sub-change counts, first 50 sub-changes; "Open job" opens `components/ConfigJobDrawer` | `/config-jobs/{id}`; POST `/{id}/continue|halt|abort|rollback` | 5 s | 1 call when a job is open |
| configuration.new | sections/NewJobForm.tsx | elements, function ref, access scope, operation, attribute JSON, waves, gate, on gate failure halt / revert, KPI guard, dry run with verdicts | POST `/config-jobs` (`dryRun` or not); `/kpi-definitions?limit=100` | — | 1 call |
| configuration.vendors | sections/Vendors.tsx | vendor capabilities: services, conformance SPEC / OWN / COMBINED, modes, schema refs | `/vendor-capabilities` | 15 s | 1 call/page |
| configuration.schemas | sections/CmSchemas.tsx | CM schemas: YANG / OPENAPI_NRM / DESCRIPTOR, revision, classes, location | `/cm-schemas` | 15 s | 1 call/page |
| configuration.trust | sections/HostKeys.tsx | O1 endpoints (health filter); the selected ssh endpoint's pinned host keys, alert when none is pinned or it is unreachable | `/o1-adaptor-endpoints?health_status=`, `/o1-adaptor-endpoints/{id}/host-keys` | 15 s / 60 s | 1 call/page + 1 |
| configuration.onboarding | sections/ElementOnboarding.tsx | discovered → selected → applying → onboarded, software check, template, config job; Select template / Apply | `/element-onboarding?status=&software_check=`; POST `/{me}/select`, `/{me}/apply` | 15 s | 1 call/page |

Only the visible tab's boxes load. Every action is a role-gated `ActionButton` / `Can` against `src/auth/permissions.fixture.json`
(the mirror of `gui-bff/app/rbac.py`); the BFF sets `requestedBy` (and an admin's MSAC tier).

## Known limits

- **Run KPI check** (`POST /config-jobs/{id}/kpi-check`) is not exposed by the GUI BFF (no rule in `rbac.py`): the KPI guard is read-only here and
  the worker's own check is the only one; the box says so.
- **Re-pin a host key** (`PUT /o1-adaptor-endpoints/{id}/host-keys`, and `DELETE …/{keyType}`) is not exposed by the GUI BFF: the keys are read-only.
- **Host-key mismatch**: the backend refuses a changed key but records no mismatch, so the page cannot say "MISMATCH". It alerts when an ssh endpoint
  has no pinned key, and says a changed key is one possible cause when the endpoint is UNREACHABLE.
- Declaring a vendor capability or loading a CM schema (admin, `PUT /vendor-capabilities/{v}`, `POST /cm-schemas`) are API-only here: the
  boxes read. "Used by" per schema (mockup) is not served.
- The job list rows carry no wave progress or requester label beyond `requestedBy` (the list route does not return waves); the detail does.
- Sub-changes of a job come in one answer (the job detail is not paged); the box counts them and lists the first 50, the drawer lists all.

## Troubleshooting

- The list is empty though jobs exist: the default filter is HALTED while the summary counts halted jobs; pick "All".
- Continue answers 409 `WAVE_PAUSE_NOT_ELAPSED`: the page sends `force` only when it saw the pause running; reload and retry.
- A new job answers 403 `MSAC_ACCESS_DENIED` with scope entire-RAN: only an admin holds the MSAC tier.
- Dry run says `WOULD_REJECT_SOME` with `ENDPOINT_UNREACHABLE`: the element's O1 endpoint is down or not registered (Endpoint trust tab).

## Upgrade notes

- v1 (GUI redesign): new page. Its New job tab is a fuller form for the same `POST /config-jobs` as the CM write form on Infrastructure → O1.
