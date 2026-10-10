# Intents

Route: `/policy` (component `Policy`)    Design: handoff `Intents.dc.html` (BRIEF §4 Intents, §4e feature 10; SCALE.md "Intents")

TS 28.312 intents dispatched to intent handlers. Tabs (URL hash): `intents` (default), `handlers`, `autonomy` (dispatches), `formulas`.

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| intents.tiles | sections/SummaryTiles.tsx | activated / total, deactivated; not fulfilled and in conflict as "—" | `/api/summary/intents` | 15 s | 1 call |
| intents.table | sections/IntentTable.tsx | intents, server-paged, `admin_state` filter; table or cards | `/intent-service/intents` | 15 s | 1 call/page |
| intents.card | sections/IntentCard.tsx | one intent: fulfilment ring, expectation, handler, actions, conflicts, negotiation feedback | `/intent-service/intent-reports?intent_id=&limit=20` | 15 s | 1 call per card |
| intents.reports | sections/IntentReports.tsx | drawer: facts, expectations, every report, admin "publish a report" | `/intent-service/intent-reports?intent_id=&limit=100` | 15 s | 1 call |
| intents.new | sections/NewIntentForm.tsx | new intent with a live handler-support check | `/intent-service/intent-handling-functions?limit=200` | 60 s | 1 call |
| intents.handlers | sections/Handlers.tsx | handlers, server-paged, deregister, admin register | `/intent-service/intent-handling-functions` | 15 s | 1 call/page |
| intents.dispatches | sections/Dispatches.tsx | autonomy dispatches, `status` filter, resolve / reject, request form | `/intent-service/autonomy-dispatches`, `/rapp-mgmt/instances` | 15 s | 1–3 calls |
| intents.formulas | sections/UtilityFormulas.tsx | TS 28.312 utility formulas (read-only) | `/intent-service/intent-utility-formulas` | 15 s | 1 call/page |

First load of the Intents tab: summary, one intents page, the handlers (new-intent form, operators) = 3 calls; selecting an intent adds 1.
The cards view reads one page of 6 intents and each card's reports (7 calls).

## Known limits

- **Fulfilment percent** (BRIEF §5): Intent Service reports FULFILLED / NOT_FULFILLED, never a percent. The ring shows the share of targets
  the newest fulfilment report marks FULFILLED ("3/4 targets met"), and "—" when that report has no per-target results.
- **"Not fulfilled" and "In conflict" tiles, the "not fulfilled first" filter**: no count or filter of them is served (the list route filters
  by admin state only); counting them would read every intent's reports. The tiles show "—".
- **Conflicts** are the ones a handler reported in the intent's newest conflict report; there is no server-side conflict detection across
  intents. The new-intent form's overlap check is not done for the same reason.
- **Negotiation feedback** (`POST /intent-service/intents/{id}/negotiation-feedback`, feature 10) is not in the BFF's permission table
  (`gui-bff/app/rbac.py`), so the card shows the offered outcomes and any feedback already given, read-only. The buttons appear by themselves
  once the BFF allows the route (they are `ActionButton`s); the satisfaction index they send is 30 / 70 / 100.
- **Utility formulas** are read-only: the BFF exposes no write on `/intent-utility-formulas`. "Used by N intents" is not served.

## Troubleshooting

- A card's ring shows "—": the handler has not published a fulfilment report with per-target results (Reports shows what it did publish).
- Activate / Deactivate is missing: only the intent's creator may change its admin state; intents created here carry RMIO `smo-gui`.
- "Create intent" answers 422: the handler's capabilities do not cover the object type, a target or the scope; the support badge says which.
