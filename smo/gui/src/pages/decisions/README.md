# Decisions

Routes: `/decisions` (`?job=<id>` / `?approval=<id>` narrow it) and `/decisions/:decisionId`    Design: `Decisions.dc.html` (PR-AI-13.4)

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| decisions.filters | sections/FilterBar.tsx | rApp, outcome, model version, range 1 h / 24 h (default) / 7 d / all, until; "Export CSV" link | — (builds the query); the link is `/ran-nf-oam/decision-records/export.csv?since&until&invoker_id&disposition` | — | 0 (a download on click) |
| decisions.tiles | sections/SummaryTiles.tsx | decisions in 24 h, autonomous, approved by a person, not written | `/api/summary/decisions` | 15 s | 1 call |
| decisions.table | sections/DecisionTable.tsx | records, server-paged without a count; row click selects | `/ran-nf-oam/decision-records?invoker_id&disposition&model_version&job_id&approval_id&since&until&total=false` | 15 s | 1 call per page |
| decisions.chain | sections/DecisionChain.tsx | inputs → model → rationale → config job → approval → verify | — (the selected row) | — | 0 |
| decisions.integrity | sections/Integrity.tsx | record hash, audit row, VERIFIED / UNCHAINED / MISMATCH | `/ran-nf-oam/decision-records/{id}` | 15 s | 1 call per selected row |
| decisions.outcome, decisions.why | sections/RecordDetail.tsx | the one-record route: outcome (job and approval drawers), why | same record | — | 0 |

## Known limits

- **Verify** (the sixth step) shows a gap note: the record carries no verification result.
- **Integrity** is per record (the record against its own audit row). The API serves no "previous hash" and no whole-chain status, so there is
  no "chain intact" claim; the box names `python -m smo_shared.audit verify`.
- **Export** is a streamed CSV download (synchronous, at most 31 days and 1,000,000 rows), not an async export job. It needs a start, so the
  "All" range cannot export; model version, job and approval filters do not apply to the file (the route does not take them).
- The table is offset-paged with `total=false`; the route's keyset `after` cursor is not used yet (deep pages of a large range are slower).
- "Rolled back" has no 24 h summary count, so the fourth tile is "not written" (rejected + lapsed + refused) instead.

## Troubleshooting

- Nothing in the list: the default range is the last 24 h; pick "All".
- A `?job=` link shows nothing: the job has no record (a job a person made), or the record is older and still being chained.
- Integrity `UNCHAINED`: the audit worker has not written the row yet; it retries.
