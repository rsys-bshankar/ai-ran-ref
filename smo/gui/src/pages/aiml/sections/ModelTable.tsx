/** Section `aiml.table`: every registered model as a server-paged table (SCALE.md P1), filtered by model type on the server (`model_type`), with
 * its lifecycle state, cleared node groups, owner and the lifecycle buttons. A row click selects the model for the detail panel. The table view
 * of the Models tab; the stage board is the other. */
import { useState, type ReactNode } from "react";

import type { Model } from "../../../api/types";
import { Card, Id, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { MODELS, useLifecycleIndex } from "../data/queries";
import { ModelActions, REGISTERED_LIFECYCLE } from "./ModelActions";

/** The table. `actions` go in the card head. */
export function ModelTable({ selected, onSelect, actions }: { selected: string | null; onSelect: (id: string) => void; actions?: ReactNode }) {
  const [modelType, setModelType] = useState("");
  const lifecycles = useLifecycleIndex();
  const lifecycleFor = (id: string) => lifecycles.data?.items.find((l) => l.modelId === id) ?? { ...REGISTERED_LIFECYCLE, modelId: id };
  return (
    <Card section="aiml.table" title="Registered models" actions={<>
      <input placeholder="Filter by model type" value={modelType} onChange={(e) => setModelType(e.target.value)} aria-label="Filter by model type" />
      {actions}
    </>}>
      <ServerTable<Model> path={MODELS} query={{ model_type: modelType.trim() || undefined }} rowKey={(m) => m.modelId}
        empty="No models registered." onRowClick={(m) => onSelect(m.modelId)} selectedKey={selected}
        columns={[
          { header: "Model", render: (m) => <><strong>{m.modelType}</strong> <span className="muted">v{m.version}</span><div className="muted small">{m.description ?? ""}</div></> },
          { header: "ID", render: (m) => <Id value={m.modelId} /> },
          { header: "State", render: (m) => <StateBadge state={lifecycleFor(m.modelId).modelLifecycleState} /> },
          { header: "Node groups", render: (m) => lifecycleFor(m.modelId).clearedNodeGroups.join(", ") || <span className="muted">—</span> },
          { header: "Owner", render: (m) => m.owner ?? <span className="muted">—</span> },
          { header: "", className: "actions", render: (m) => <ModelActions model={m} lifecycle={lifecycleFor(m.modelId)} /> },
        ]} />
    </Card>
  );
}
