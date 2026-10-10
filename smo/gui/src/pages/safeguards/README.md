# Safeguards

Route: `/safeguards`    Design: `Safeguards.dc.html`, SCALE.md "Safeguards"

Tabs (URL hash): `#limits` · `#refusals` · `#watchers`. "Stop all rApp writes" sits in the page header.

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| safeguards.stopall | sections/GlobalStop.tsx | Stop all rApp writes: count, confirm with reason, loop of per-rApp stops, result | `/rapp-mgmt/instances` (all pages, on click), `PUT /rapp-mgmt/instances/{id}/kill` each | — | 0 on load |
| safeguards.tiles | sections/SummaryTiles.tsx | instances, stopped, refusals 24 h | `/api/summary/rapps`, `/ran-nf-oam/rapp-kill?limit=1`, `/ran-nf-oam/safeguard-refusals?since&limit=1` | 15 s | 3 calls |
| safeguards.limits | sections/LimitsTable.tsx (+ Dialogs.tsx) | per-rApp table: mode, writes, change/write, jobs/h used vs max, elements/job, hold, actions | `/rapp-mgmt/instances`, `/rapp-mgmt/instances/{id}/safeguards` per row | 15 s | 1 + 1 per row |
| safeguards.detail | sections/RappDetail.tsx | selected rApp: stop details, meters, limits, approval, refusals 24 h | same safeguards call, `/ran-nf-oam/safeguard-refusals?invoker_id&since&limit=1` | 15 s | 1 call |
| safeguards.stopped | sections/Stopped.tsx | everything stopped at RAN NF OAM | `/ran-nf-oam/rapp-kill` | 15 s | 1 call |
| safeguards.refusals | sections/Refusals.tsx | refusals, range 24 h / 7 d (default) / 30 d / all, reason, invoker | `/ran-nf-oam/safeguard-refusals?code&invoker_id&since` | 15 s | 1 call |
| safeguards.watchers | sections/Watchers.tsx | webhooks told about refusals, Add / Remove | `/ran-nf-oam/safeguard-subscriptions` | 15 s | 1 call |

## Known limits

- **Stop all** is not one server call: the backend has no global stop (SCALE.md §5 ask 7). The button reads every instance that is not
  UNDEPLOYED, confirms with that count, then stops them one by one; an rApp already stopped is stopped again with the new reason. Shown only to a
  role allowed `PUT /rapp-mgmt/instances/{id}/kill`.
- Each table row reads its instance's safeguards (one call per row of the page): there is no list route of safeguards.
- The refusal count per rApp is in the detail card only (one more call per row would double the page's calls).
- "Near their rate limit" and "holding for approval" tiles, table filters on them, and bulk "hold selected" are not built: no server count or filter.

## Troubleshooting

- A row says "no credential": the instance is terminated, nothing to stop or limit.
- Stop all reports failures: each failed instance is listed with the error its stop call answered.
- No Approval… / Stop holding: setting an approval policy is an admin's decision.
