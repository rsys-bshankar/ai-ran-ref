# Alarms

Route: `/alarms` (`?me=<managed element>` starts the table filtered on it; the global search links there)    Design: `Alarms.dc.html`, SCALE.md "Alarms · at scale"

Tabs (URL hash): `#ran` RAN NF alarms · `#ocloud` O-Cloud alarms · `#fm` FM subscriptions. Only the visible tab's queries run.

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| alarms.tiles | sections/SeverityTiles.tsx | count per severity (toggles the table filter), open total, time to acknowledge (—) | `/api/summary/alarms` | 15 s | 1 call (shared with the tab counts) |
| alarms.table | sections/AlarmTable.tsx | RAN alarms, server-paged; filters severity, managed element, managed function (route params), ack state and show cleared (this page only) | `/ran-nf-oam/alarms?severity&managed_element_ref&managed_function_ref&limit&offset` | 5 s | 1 call per page |
| alarms.detail | sections/AlarmDetail.tsx | selected alarm: Ack/Unack/Clear, TS 28.532 fields, lifecycle timeline | — (the table's row) | with the table | 0 |
| alarms.rootcause | sections/RootCauseHint.tsx | heuristic: other alarms of the same element within 60 s | `/ran-nf-oam/alarms?managed_element_ref=<me>&limit=20&total=false` | 15 s | 1 call per selected alarm |
| alarms.inject | sections/InjectAlarm.tsx | admin: inject a test alarm | `POST /ran-nf-oam/alarms/ingest` | — | 0 |
| alarms.ocloud | sections/OCloudAlarms.tsx | FOCOM alarms, server-paged, severity / resource filters | `/focom/alarms?severity&resource_ref` | 5 s | 1 call |
| alarms.fm | sections/FmSubscriptions.tsx | new FM subscription, list with Unsubscribe | `/ran-nf-oam/fm-subscriptions`, `/ran-nf-oam/o1-adaptor-endpoints` | 15 s | 2 calls |

`sections/AlarmActions.tsx` holds the Ack / Unack / Clear buttons the table and the detail share.

First load (RAN tab): summary + one page = 2 calls (SCALE.md §4 budget).

## Known limits

- **Mean time to acknowledge** shows "—": the backend keeps who acknowledged an alarm but not when.
- **Root-cause hint** is a client heuristic (same managed element, raised within 60 s, among the newest 20 alarms of that element), labelled as such.
  Server-side correlation is `PR-MGT-9` / `MGT-10`.
- **Group by** probable cause / element / cluster and bulk ack on a group are not built: they need group-by counts on the alarm route
  (SCALE.md §5 ask 4). Rows are sorted most severe, then newest, within the page only.
- **Ack state** and **show cleared** have no route parameter, so they narrow the current page only; the table says how many rows they hid.
- The managed element filter is a text box (exact match on `managed_element_ref`), not a list built from every alarm.
- No pushed updates (SSE, SCALE.md P7) and no "N new" bar yet: the visible page polls every 5 s. No 24 h trend sparkline (needs bucketed counts).
- The lifecycle has no acknowledge time (not stored); "Assign…" from the mockup has no backend.

## Troubleshooting

- Tiles show "—": the summary's module did not answer (`partial` is listed under the tiles); check `/api/summary/alarms`.
- Table empty with a `?me=` link: the element name must match `managedElementRef` exactly.
- Ack / Clear missing: the role lacks `PATCH /ran-nf-oam/alarms/{id}/ack|clear` (operator and up).
