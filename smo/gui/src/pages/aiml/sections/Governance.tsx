/** Section `aiml.governance` (feature 7): the selected model's governance and lifecycle history, server-paged: every governance decision
 * (`GET /aimgf/models/{id}/governance-history`: decision, decided by, rationale) or every transition of the model and runtime FSMs
 * (`/lifecycle-history?fsm=`: event, from → to). Below it, Roll back / Deprecate / Retire with a rationale, offered only where the model's state
 * allows the event and the BFF lets the role send it (admin: `advance` with that `event`). AIMgF records the rationale for a governance event
 * (ROLLBACK); DEPRECATE and RETIRE are not governance events, so they appear in the lifecycle history only. */
import { useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { ActionButton, Card, Field } from "../../../components/ui";
import { formatTime } from "../../../lib/domain";
import { Badge } from "../../../kit/Badge";
import { Segmented } from "../../../kit/Segmented";
import { ServerTable } from "../../../kit/ServerTable";
import { aimgfModel, governanceHistoryPath, lifecycleHistoryPath, lifecycleHistoryQuery, useModelLifecycle, type CertificationRecord, type LifecycleTransition } from "../data/queries";

type View = "governance" | "all" | "MODEL" | "RUNTIME";

/** The end-of-life events legal from a model state (aimgf/app/statemachine.py): PROMOTED rolls back to CERTIFIED or is deprecated, CERTIFIED is
 * deprecated, DEPRECATED and FAILED are retired. */
export function endOfLifeEvents(state: string): { event: "ROLLBACK" | "DEPRECATE" | "RETIRE"; label: string }[] {
  switch (state) {
    case "PROMOTED": return [{ event: "ROLLBACK", label: "Roll back to CERTIFIED" }, { event: "DEPRECATE", label: "Deprecate" }];
    case "CERTIFIED": return [{ event: "DEPRECATE", label: "Deprecate" }];
    case "DEPRECATED": case "FAILED": return [{ event: "RETIRE", label: "Retire" }];
    default: return [];
  }
}

/** The box. */
export function Governance({ id, title }: { id: string; title?: string }) {
  const [view, setView] = useState<View>("governance");
  return (
    <Card section="aiml.governance" title={`${title ? `${title} · ` : ""}Governance & lifecycle history`}
      sub="Every decision with who and why; every transition of the model and its runtime"
      actions={<Segmented<View> label="History" value={view} onChange={setView} options={[
        { id: "governance", label: "Governance" }, { id: "all", label: "All transitions" }, { id: "MODEL", label: "Model" }, { id: "RUNTIME", label: "Runtime" },
      ]} />}>
      {view === "governance"
        ? <ServerTable<CertificationRecord> path={governanceHistoryPath(id)} rowKey={(r) => r.certificationRecordId} empty="No governance decision recorded yet." columns={[
          { header: "When", render: (r) => <span className="mono xs muted">{formatTime(r.decidedAt)}</span> },
          { header: "Decision", render: (r) => <Badge tone="volt" plain>{r.decision}</Badge> },
          { header: "Decided by", render: (r) => r.decidedBy },
          { header: "Rationale", render: (r) => r.rationale ?? <span className="muted">—</span> },
        ]} />
        : <ServerTable<LifecycleTransition> path={lifecycleHistoryPath(id)} query={lifecycleHistoryQuery(view === "all" ? "" : view)}
          rowKey={(r) => `${r.fsm}/${r.event}/${r.occurredAt}`} empty="No transition recorded yet." columns={[
            { header: "When", render: (r) => <span className="mono xs muted">{formatTime(r.occurredAt)}</span> },
            { header: "FSM", render: (r) => r.fsm },
            { header: "Event", render: (r) => <Badge tone="info" plain>{r.event}</Badge> },
            { header: "From → to", render: (r) => <span className="mono small">{r.fromState === r.toState ? r.toState : `${r.fromState} → ${r.toState}`}</span> },
          ]} />}
      <EndOfLife id={id} />
    </Card>
  );
}

/** Roll back / Deprecate / Retire with a rationale. Nothing renders when the state allows none or the role may send none. */
function EndOfLife({ id }: { id: string }) {
  const lifecycle = useModelLifecycle(id);
  const { can } = useAuth();
  const [rationale, setRationale] = useState("");
  const path = `${aimgfModel(id)}/advance`;
  const events = endOfLifeEvents(lifecycle.data?.modelLifecycleState ?? "").filter((e) => can("POST", path, { event: e.event }));
  if (events.length === 0) return null;
  return (
    <div className="inset col">
      <Field label="Rationale" hint="Required. AIMgF keeps it with a roll back (a governance decision); deprecate and retire show in the lifecycle history.">
        <input value={rationale} onChange={(e) => setRationale(e.target.value)} placeholder="Why this model leaves service" />
      </Field>
      <div className="row wrap">
        {events.map((e) => (
          <ActionButton key={e.event} label={e.label} tone={e.event === "ROLLBACK" ? "danger" : "default"} disabled={!rationale.trim()}
            confirm={e.event === "ROLLBACK" ? undefined : `${e.label} this model? This is terminal.`}
            action={{ method: "POST", path, query: { event: e.event, decided_by: "smo-gui", rationale: rationale.trim() }, success: `${e.event} → done` }}
            onDone={() => setRationale("")} />
        ))}
      </div>
    </div>
  );
}
