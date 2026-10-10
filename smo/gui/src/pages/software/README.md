# Software

Route: `/software` (tabs in the hash: `#campaigns`, `#jobs`, `#new`; `?campaign=<id>`, `?wave=<n>`, `?status=<state>`)    Design: handoff `Software.dc.html` (BRIEF §4e feature 2)

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| software.tiles | sections/CampaignTiles.tsx | running · halted (filters the list), completed, rolled back, all with a state meter | `/api/summary/software` (`campaigns.*`) | 15 s | 1 call (shared with the tab count) |
| software.list | sections/CampaignList.tsx | campaigns, server-paged, state filter | `/ran-nf-oam/software-campaigns?status=&limit=&offset=` | 15 s | 1 call/page |
| software.detail | sections/CampaignDetail.tsx (+ CampaignActions.tsx, Countdown.tsx) | selector and settings, halted reason (GATE_FAILED / WAVE_PAUSE with countdown / OPERATOR_HALT), Continue / Halt / Roll back / Abort, totals, wave strip, event log | `/software-campaigns/{id}`, `/software-campaigns/{id}/report`; POST `/{id}/continue|halt|abort|rollback` | 5 s / 10 s | 2 calls when a campaign is open |
| software.elements | sections/CampaignElements.tsx | the elements of one wave, each job linking to `/flows/19?subject=<job>`, each element to `/elements/<me>` | the report (shared) | 10 s | 0 extra |
| software.jobs | sections/ElementJobs.tsx | every element software job (flow 19), server-paged, element filter | `/software-management-jobs?managed_element_ref=` | 15 s | 1 call/page |
| software.new | sections/NewCampaignForm.tsx (+ data/form.ts) | selector or element list, wave size, pause, gate, on gate failure; dry run first, then Start | POST `/software-campaigns` (`dryRun: true`, then without); `/vendor-capabilities?limit=100` for vendor suggestions | — | 1 call |

Actions are role-gated (`ActionButton`, `Can`; operator in `gui-bff/app/rbac.py`, which also sets `requestedBy`). Roll back and Abort ask
first; Continue during an unexpired pause asks and sends `force: true`. `campaignActions` (data/types.ts) mirrors the states the backend accepts.

## Known limits

- The mockup's tiles "Elements on 24.3.x %", "Gate failures · 7 d" and "Element jobs failed · 24 h" are not shown: no route serves a
  software-version share, gate failures over time or failed jobs by time (the jobs list has no time or status filter).
- Elements per wave come from the campaign report, which holds every wave of the campaign in one answer; the box pages them 50 at a time in the
  browser. A paged per-wave route would be needed for campaigns of many thousands of elements.
- The element job list has no status or time filter (the route takes only `managed_element_ref`), so "failed first" is not offered.
- "New alarms" per element (mockup column) is not served; the gate's own detail says how many alarms failed it.
- Region, tenant and entity type in the form are free text: no route lists the values in use.

## Troubleshooting

- Continue answers 409 `WAVE_PAUSE_NOT_ELAPSED`: the pause has not ended; the button sends `force` only when the page saw the pause running.
- Roll back answers 422 `ROLLBACK_NOT_POSSIBLE`: a job of the campaign is still running; wait for it (the detail refreshes every 5 s).
- Start stays disabled: run "Dry run" with the current values first (any change to the form needs a new dry run).

## Upgrade notes

- v1 (GUI redesign): new page.
