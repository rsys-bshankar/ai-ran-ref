/** Section `aiml.board`: the models as a kanban by lifecycle stage (Registered, Training, Validating / emulating, Promoted, Active runtime), with
 * each column's count and its first five cards, "+N more" to open the rest, and a red outline (plus the words "under floor") on a model whose
 * newest MLMF report breached its guard-KPI floor. A click selects the model for the detail panel.
 *
 * Reads four bounded lists (models, lifecycles, MLMF subscriptions, recent MLMF reports; data/queries.ts) and groups them with `data/board.ts`:
 * the backend has no count by stage, so the counts cover the first BOARD_LIMIT models and the box says so when there are more. */
import { useState, type ReactNode } from "react";

import { Card, StateBadge } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { formatCount } from "../../../kit/Kpi";
import { ErrorRetry, Skeleton } from "../../../kit/states";
import { buildBoard, breachedModels } from "../data/board";
import { BOARD_LIMIT, useLifecycleIndex, useMlmfSubscriptionIndex, useModelIndex, useRecentMlmfReports } from "../data/queries";
import type { BoardColumn } from "../data/types";

/** How many cards a column shows before "+N more". */
const TOP = 5;

/** The board. `actions` go in the card head (register, view switch). */
export function StageBoard({ selected, onSelect, actions }: { selected: string | null; onSelect: (id: string) => void; actions?: ReactNode }) {
  const models = useModelIndex();
  const lifecycles = useLifecycleIndex();
  const subs = useMlmfSubscriptionIndex();
  const reports = useRecentMlmfReports();
  const failed = models.error ?? lifecycles.error;
  const ready = models.data && lifecycles.data;
  const board = ready ? buildBoard(models.data!.items, lifecycles.data!.items, breachedModels(subs.data?.items ?? [], reports.data?.items ?? [])) : null;
  const total = models.data?.total;
  const cut = total !== undefined && models.data ? total > models.data.items.length : false;
  return (
    <Card section="aiml.board" title="Models by stage" sub="AIMgF lifecycle · MLMF guard floor" actions={actions}>
      {failed && !ready ? <ErrorRetry error={failed} onRetry={() => { void models.refetch(); void lifecycles.refetch(); }} />
        : !board ? <Skeleton lines={4} />
          : board.columns.every((c) => c.cards.length === 0) && Object.keys(board.off).length === 0 ? <p className="muted">No models registered.</p>
            : <>
              <div className="kanban" role="list" aria-label="Models by stage">
                {board.columns.map((c) => <Column key={c.id} column={c} selected={selected} onSelect={onSelect} />)}
              </div>
              <div className="row between wrap">
                <span className="small muted">
                  {Object.entries(board.off).map(([s, n]) => `${n} ${s.toLowerCase()}`).join(" · ") || "No failed, deprecated or retired models."}
                  {Object.keys(board.off).length > 0 && " (off the board; find them in the table view)"}
                </span>
                {(subs.error || reports.error) && <span className="gap-note">Guard-floor outlines unavailable: MLMF did not answer.</span>}
              </div>
              <p className="gap-note">
                Cards are in registry order: the backend serves no "last changed" time per model.
                {cut && ` Counts cover the first ${formatCount(BOARD_LIMIT)} of ${formatCount(total)} models (no count by stage is served).`}
              </p>
            </>}
    </Card>
  );
}

/** One column: title, count, the first five cards (or all, once expanded). */
function Column({ column, selected, onSelect }: { column: BoardColumn; selected: string | null; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const shown = open ? column.cards : column.cards.slice(0, TOP);
  const more = column.cards.length - TOP;
  return (
    <div className="kanban-col" role="listitem" aria-label={`${column.title}: ${column.cards.length}`} data-stage={column.id}>
      <div className="row between"><span className="eyebrow">{column.title}</span><Badge plain>{column.cards.length}</Badge></div>
      {shown.map((m) => (
        <button key={m.modelId} type="button" className={`kanban-card${m.breach ? " breach" : ""}${selected === m.modelId ? " on" : ""}`}
          aria-pressed={selected === m.modelId} onClick={() => onSelect(m.modelId)}>
          <span className="row between"><strong className="small">{m.name}</strong><span className="mono xs muted">{m.version}</span></span>
          {m.useCase && <span className="xs muted">{m.useCase}</span>}
          <span className="row between wrap"><StateBadge state={m.state} />{m.breach && <Badge tone="bad">under floor</Badge>}</span>
        </button>
      ))}
      {column.cards.length === 0 && <span className="xs muted">None</span>}
      {more > 0 && <button type="button" className="btn ghost small" onClick={() => setOpen(!open)}>{open ? "Show fewer" : `+ ${more} more in this stage`}</button>}
    </div>
  );
}
