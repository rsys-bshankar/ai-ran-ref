/** Element detail · cell guards (`element.guards`): the guard of each cell of this element, paged by the server
 * (`GET /cell-guards?managed_element_ref=`): its class (NORMAL, COVERAGE_CRITICAL, EMERGENCY), sector group, incident zone and neighbours.
 * Every rApp reads these before it acts on a cell. "Edit" and "Add a cell guard" open the editor (`?cell=`); they show only to a role that may
 * set a guard (admin in gui-bff/app/rbac.py). */
import { Can, Card, type Column } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Callout } from "../../../kit/Callout";
import { ServerTable } from "../../../kit/ServerTable";
import { GUARDS_PATH, guardPath, useEditedCell } from "../data/queries";
import type { CellGuardRow } from "../data/types";

/** The tone of a cell class. */
export const CLASS_TONE = { NORMAL: "mute", COVERAGE_CRITICAL: "warn", EMERGENCY: "bad" } as const;

/** The guards card of element `me`. */
export function CellGuards({ me }: { me: string }) {
  const [cell, edit] = useEditedCell();
  const columns: Column<CellGuardRow>[] = [
    { header: "Cell", render: (g) => <span className="mono small">{g.cellId}</span> },
    { header: "Class", render: (g) => <Badge tone={CLASS_TONE[g.cellClass] ?? "mute"}>{g.cellClass}</Badge> },
    { header: "Sector group", render: (g) => <span className="mono small">{g.sectorGroup ?? "—"}</span> },
    { header: "Incident zone", render: (g) => <span className="mono small">{g.incidentZone ?? "—"}</span> },
    { header: "Neighbours", render: (g) => <span className="small">{g.neighbourRefs.length}{g.neighbourRefs.length ? ` · ${g.neighbourRefs.slice(0, 2).join(", ")}${g.neighbourRefs.length > 2 ? "…" : ""}` : ""}</span> },
    { header: "", className: "actions", render: (g) => (
      <Can method="PUT" path={guardPath(me, g.cellId)}><button type="button" className="btn small" onClick={() => edit(g.cellId)}>Edit</button></Can>
    ) },
  ];
  return (
    <div className="stack" data-section="element.guards">
      <Callout tone="volt">Cell guards tell every rApp how carefully to treat a cell. The Energy Saving rApp, for one, never sleeps an EMERGENCY or
        COVERAGE_CRITICAL cell, nor two cells of one sector group at once.</Callout>
      <Card title="Cells and their guards" sub={`/managed-entities/${me}/cells/{cell}/guards`}
        actions={<Can method="PUT" path={guardPath(me, "new")}><button type="button" className="btn small" onClick={() => edit("+")}>Add a cell guard</button></Can>}>
        <ServerTable<CellGuardRow> path={GUARDS_PATH} query={{ managed_element_ref: me }} columns={columns} rowKey={(g) => g.cellId} selectedKey={cell}
          empty="No cell of this element has a guard: every rApp treats its cells as NORMAL." />
      </Card>
    </div>
  );
}
