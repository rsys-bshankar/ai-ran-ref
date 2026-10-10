/** The stage board's pure logic: which column a model's lifecycle puts it in, which models' latest MLMF report is under the floor, and the
 * artifact versions a model has. No React and no fetching, so `__tests__/board.test.ts` pins it down directly. The columns are the five of the
 * BRIEF (§4 AI/ML); the lifecycle states are AIMgF's (aimgf/app/statemachine.py), mirrored in `lib/domain.ts` MODEL_PIPELINE. */
import type { MlmfReport, MlmfSubscription, Model, ModelLifecycle } from "../../../api/types";
import type { BoardCard, BoardColumn, StageId } from "./types";

/** The column titles, in board order. */
export const STAGES: { id: StageId; title: string }[] = [
  { id: "registered", title: "Registered" },
  { id: "training", title: "Training" },
  { id: "validating", title: "Validating / emulating" },
  { id: "promoted", title: "Promoted" },
  { id: "active", title: "Active runtime" },
];

/** States that are off the board: the end of life and a failed stage (the board shows their count, not cards). */
export const OFF_BOARD = ["FAILED", "DEPRECATED", "RETIRED"] as const;

/** The column of a model, or null when it is off the board. An ACTIVE runtime wins over the model state (a DEPRECATED model can keep serving). */
export function stageOf(state: string, runtime: string): StageId | null {
  if (runtime === "ACTIVE" && state !== "RETIRED") return "active";
  switch (state) {
    case "REGISTERED": return "registered";
    case "TRAINING": case "TRAINED": return "training";
    case "VALIDATING": case "VALIDATED": case "EMULATING": case "EMULATED": case "PENDING_APPROVAL": case "APPROVED": return "validating";
    case "CERTIFIED": case "PROMOTED": return "promoted";
    default: return null;
  }
}

/** Model ids whose newest report (among `reports`, newest first as AIMgF answers) of any of their subscriptions breached its floor. */
export function breachedModels(subscriptions: MlmfSubscription[], reports: MlmfReport[]): Set<string> {
  const modelOf = new Map(subscriptions.map((s) => [s.subscriptionId, s.modelId]));
  const seen = new Set<string>();
  const out = new Set<string>();
  for (const r of reports) {
    if (seen.has(r.subscriptionId)) continue;          // only the newest report of each subscription counts
    seen.add(r.subscriptionId);
    const model = modelOf.get(r.subscriptionId);
    if (model && r.breachedFloor) out.add(model);
  }
  return out;
}

/** The board: every model in its column (a model AIMgF has no lifecycle row for is REGISTERED, as AIMgF itself defaults it), plus the
 * off-board counts by state. Cards keep the order of `models` (MLMR's list order: the backend serves no "last changed" time). */
export function buildBoard(models: Model[], lifecycles: ModelLifecycle[], breached: Set<string>): { columns: BoardColumn[]; off: Record<string, number> } {
  const byId = new Map(lifecycles.map((l) => [l.modelId, l]));
  const columns: BoardColumn[] = STAGES.map((s) => ({ ...s, cards: [] }));
  const off: Record<string, number> = {};
  for (const m of models) {
    const l = byId.get(m.modelId);
    const state = l?.modelLifecycleState ?? "REGISTERED";
    const runtime = l?.runtimeLifecycleState ?? "NOT_DEPLOYED";
    const stage = stageOf(state, runtime);
    if (!stage) { off[state] = (off[state] ?? 0) + 1; continue; }
    const card: BoardCard = { modelId: m.modelId, name: m.modelType, version: m.version, useCase: m.description, state, runtime, breach: breached.has(m.modelId) };
    columns.find((c) => c.id === stage)!.cards.push(card);
  }
  return { columns, off };
}

/** The artifact versions of a model, newest first: MLMR's `artifactLocation` ends in ":<latest version>" and versions count up from 1. */
export function artifactVersions(artifactLocation: string | null | undefined): number[] {
  const latest = artifactLocation ? Number(artifactLocation.split(":").pop()) : 0;
  if (!Number.isInteger(latest) || latest <= 0) return [];
  return Array.from({ length: latest }, (_, i) => latest - i);
}
