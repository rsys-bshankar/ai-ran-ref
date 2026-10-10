/** Element detail · guard editor (`element.guard-editor`): set the guard of one cell (`PUT /managed-entities/{me}/cells/{cell}/guards`: class,
 * sector group, incident zone, neighbour cells) or remove it (`DELETE`, asked first). Opens for the cell in `?cell=` ("+" for a new cell id),
 * starting from the guard the element holds (`GET /managed-entities/{me}`). Shown only to a role that may write guards (admin, `Can`). */
import { useEffect, useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import { ActionButton, Can, Card, Field } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { Segmented } from "../../../kit/Segmented";
import { guardPath, useEditedCell, useEntity } from "../data/queries";
import { CELL_CLASSES, type CellClass } from "../data/types";

/** The PUT body from the editor's text: blank group and zone are null, neighbours one per line (or comma-separated), duplicates dropped. */
export function guardBody(f: { cellClass: CellClass; sectorGroup: string; incidentZone: string; neighbours: string }) {
  const neighbourRefs = [...new Set(f.neighbours.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean))];
  return { cellClass: f.cellClass, sectorGroup: f.sectorGroup.trim() || null, incidentZone: f.incidentZone.trim() || null, neighbourRefs };
}

/** The editor of element `me`, or nothing when no cell is open. */
export function GuardEditor({ me }: { me: string }) {
  const [cell, close] = useEditedCell();
  const entity = useEntity(me);
  const isNew = cell === "+";
  const current = cell && !isNew ? entity.data?.cellGuards[cell] : undefined;
  const [cellId, setCellId] = useState("");
  const [cellClass, setCellClass] = useState<CellClass>("NORMAL");
  const [sectorGroup, setSectorGroup] = useState("");
  const [incidentZone, setIncidentZone] = useState("");
  const [neighbours, setNeighbours] = useState("");
  const [saved, setSaved] = useState(false);
  const save = useSmoAction();
  useEffect(() => {
    setCellId(isNew ? "" : cell ?? "");
    setCellClass(current?.cellClass ?? "NORMAL");
    setSectorGroup(current?.sectorGroup ?? "");
    setIncidentZone(current?.incidentZone ?? "");
    setNeighbours((current?.neighbourRefs ?? []).join("\n"));
    setSaved(false);
  }, [cell, isNew, JSON.stringify(current ?? null)]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!cell) return null;
  const target = cellId.trim();
  return (
    <Can method="PUT" path={guardPath(me, target || "cell")}>
      <Card section="element.guard-editor" title={isNew ? "New cell guard" : `Edit guard · ${cell}`} actions={<button type="button" className="btn small ghost" onClick={() => close(null)}>Close</button>}>
        <form className="form" onSubmit={(e) => {
          e.preventDefault();
          if (!target) return;
          save.mutate({ method: "PUT", path: guardPath(me, target), json: guardBody({ cellClass, sectorGroup, incidentZone, neighbours }), success: `Guard of ${target} saved` },
            { onSuccess: () => setSaved(true) });
        }}>
          {isNew && <Field label="Cell id"><input className="mono" value={cellId} onChange={(e) => setCellId(e.target.value)} required /></Field>}
          <Field label="Cell class"><Segmented label="Cell class" value={cellClass} onChange={setCellClass} options={CELL_CLASSES.map((c) => ({ id: c, label: c }))} /></Field>
          <Field label="Sector group" hint="Cells of one group are never slept at once"><input className="mono" value={sectorGroup} onChange={(e) => setSectorGroup(e.target.value)} /></Field>
          <Field label="Incident zone"><input className="mono" value={incidentZone} onChange={(e) => setIncidentZone(e.target.value)} /></Field>
          <Field label="Neighbour cells" hint="One cell id per line; they make the relations on the RAN topology page">
            <textarea rows={4} className="mono" value={neighbours} onChange={(e) => setNeighbours(e.target.value)} spellCheck={false} />
          </Field>
          <div className="row wrap">
            <button type="submit" className="btn primary" disabled={!target || save.isPending}>{save.isPending ? "…" : "Save guard"}</button>
            {!isNew && current && <ActionButton label="Remove guard" tone="danger" confirm={`Remove the guard of ${cell}? rApps then treat it as NORMAL.`}
              action={{ method: "DELETE", path: guardPath(me, cell), success: `Guard of ${cell} removed` }} onDone={() => close(null)} />}
          </div>
          {saved && <Callout tone="volt">Saved · rApps read the new guard on their next query.</Callout>}
        </form>
      </Card>
    </Can>
  );
}
