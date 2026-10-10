# RAN topology

Route: `/topology` (`?me=<element>` focuses one element, `?problem=external|ambiguous` picks the problem table's filter)    Design: handoff `Topology.dc.html` (BRIEF §4e feature 1)

The Dashboard's "Open topology" link lands here. Every element in a graph node, a problem row or the summary links to its Element detail page (`/elements/<me>`).

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| topology.export | sections/ExportTeiv.tsx | "Export (TEIV JSON)" download in the header | `GET /ran-nf-oam/topology[?managed_element_ref]` | on click | 0 on load |
| topology.tiles | sections/RelationTiles.tsx | cells with guards, elements, relations, not reciprocal, external, ambiguous (problem tiles filter the table) | `/topology/links` (shared), `/cell-guards?limit=1` total, `/managed-entities?limit=1` total | 60 s | 3 calls |
| topology.graph | sections/NeighbourGraph.tsx | the focused element, its cells and first-ring neighbours (≤ 200 nodes, dashed = not reciprocal); picker with suggestions | `/topology/links?managed_element_ref`, `/managed-entities/{me}`, `/managed-entities?limit=100` (suggestions) | 60 s | 1 + 2 when focused |
| topology.check | sections/RelationCheck.tsx | how DN A stands to DN B in the containment tree | `/topology/relation?a&b` | on Check | 0 on load |
| topology.element | sections/ElementSummary.tsx | vendor, type, region/tenant, cells, sector groups, incident zones, non-NORMAL guards, links | `/managed-entities/{me}` (shared with the graph) | 60 s | 0 extra |
| topology.problems | sections/ProblemRelations.tsx | relations that need attention (not reciprocal / external / ambiguous), everywhere or around the focused element, with the fix and a link to the cell guards | `/topology/links` (shared with the tiles) or `?managed_element_ref` (shared with the graph) | 60 s | 0 extra |

Pure rules (counts, problem filter, fix hint, graph layout and the 200-node cap) are in `data/graph.ts`.

## Known limits

- `/topology/links` is not paged and has no `reciprocal` filter: the page reads the whole list once (shared by the tiles and the problem
  table) and counts, filters and pages it (50 rows) in the browser. The counts are true totals, but at 10k elements the answer is large.
  Backend ask: `limit`/`offset` and a `reciprocal=false` filter on `/topology/links`, or relation counts in `/bff/summary` (SCALE.md P1/P2).
- `/managed-entities` has no name search: the focus picker suggests the first 100 elements and otherwise takes the exact ref typed.
- The mockup's "Cells in scope … DUs · metro-a" is shown as "cells with guards" (only a cell with a guard is known to the registry) and the
  total of managed elements; there is no region scope on the links route.
- Alarm badges on cells (the mockup's "cell-7 LOS") are not shown: no route joins alarms to cells per element in one call.
- The graph shows only relations declared in cell guards (`neighbourRefs`); TEIV RAN-domain relations (Xn, F1) are not modelled by the backend.

## Troubleshooting

- Graph says "declares no neighbour relations": the element's cell guards have no `neighbourRefs` (Element detail → Cell guards).
- Relation check answers "Not in the containment tree": a DN is not a node of `/managed-objects` (compare exact text, case-sensitive);
  refresh the element's tree or register its function.
- Tiles show "—": `/topology/links` failed; the box shows the error with Retry. Check RAN NF OAM's logs.

## Upgrade notes

- v1 (GUI redesign): new page.
