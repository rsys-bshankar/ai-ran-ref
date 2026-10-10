# Approvals

Route: `/approvals`    Design: `Approvals.dc.html` (BRIEF §4 "Approvals", PR-GUI-7)

Tabs (URL hash): `#waiting` (queue and detail side by side) · `#decided`. `ApprovalDrawer` (the detail in a drawer) is exported from
`index.tsx` for the Decisions page.

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| approvals.queue | sections/Queue.tsx | waiting requests as cards with a lapse countdown bar; first one selected | `/ran-nf-oam/rapp-approvals?status=PENDING&total=false` | 5 s | 1 call per page |
| approvals.detail | sections/Detail.tsx | title, status, countdown clock, facts; hosts the boxes below | `/ran-nf-oam/rapp-approvals/{id}` (+ `/decision-records?approval_id=` once decided) | 5 s | 1–2 calls |
| approvals.impact | sections/ImpactTiles.tsx | changes, managed elements, access scope, what a lapse does | — (the request) | — | 0 |
| approvals.diff | sections/ChangeDiff.tsx | the config change as a diff (`kit/Diff`), 50 lines then "… N more" | — | — | 0 |
| approvals.why | sections/Rationale.tsx | rationale, input chips, link to the decision record | — | — | 0 |
| approvals.decide | sections/DecisionBox.tsx | reason, Approve & write, Reject; then the result (status, config job) | `POST …/{id}/approve`, `POST …/{id}/reject` | — | 0 |
| approvals.decided | sections/Decided.tsx | decided and lapsed requests, outcome filter, drawer | `/ran-nf-oam/rapp-approvals?status&total=false` | 15 s | 1 call per page |

The tab badge is `approvals.PENDING` from `/api/summary/approvals` (never the length of a page).

## Known limits

- **Expected KPI impact** (handover success, ping-pong, model confidence) is not shown: the rApp sends no prediction with the request.
- **Safeguard check badges** are not shown: the request carries no check results (the checks run again on approve; a refusal shows as `REFUSED` with its code).
- **The diff has only "+" lines**: the request carries the new values, not the current ones. No server-side diff summary or "download full diff".
- "Approve similar for 1 hour", grouped queue, bulk approve of a group and Undo are not built (no backend support; BRIEF §5).
- Decided with "Every outcome": the route has no "not pending" filter, so pending rows of the page are left out in the browser.
- Change-window approvals (GUI-7.1) and model gate approvals (GUI-7.3) are not in this inbox yet; the page says so.

## Troubleshooting

- No Approve / Reject: the role lacks `POST /ran-nf-oam/rapp-approvals/{id}/approve` (operator and up), or the request is no longer pending.
- Approve answers `REFUSED`: a safeguard (stopped rApp, a limit) refused it when it ran; see Safeguards → Refusals.
- Queue empty but the badge counts: the summary is cached 5 s by the BFF; it catches up on the next refresh.
