/** Unit tests of the AI/ML stage board's pure logic (`aiml/data/board.ts`) and the end-of-life rules of the governance section: which column a
 * lifecycle state lands in, which models are under their MLMF floor (newest report only), the off-board counts, the artifact versions read from
 * `artifactLocation`, and which of roll back / deprecate / retire a state allows. No DOM, no fetch. Run: `npx vitest run src/pages/aiml` from smo/gui. */
import { describe, expect, it } from "vitest";

import type { MlmfReport, MlmfSubscription, Model, ModelLifecycle } from "../../../api/types";
import { artifactVersions, breachedModels, buildBoard, stageCounts, stageOf } from "../data/board";
import { endOfLifeEvents } from "../sections/Governance";

const model = (id: string, type = "m"): Model => ({ modelId: id, modelType: type, version: "1", artifactLocation: null, description: null, author: null, owner: null, inputDataType: null, outputDataType: null, targetEnvironments: [] });
const life = (id: string, state: string, runtime = "NOT_DEPLOYED"): ModelLifecycle => ({ modelId: id, modelLifecycleState: state, runtimeLifecycleState: runtime, trainingJobId: null, clearedNodeGroups: [], nfDeploymentDescriptorId: null, nfDeploymentId: null, trainingApproved: false, validationApproved: false });
const sub = (id: string, modelId: string): MlmfSubscription => ({ subscriptionId: id, modelId, metricTypes: ["accuracy"], dmeTypeId: "d", guardKpiFloor: { accuracy: 0.9 }, notificationDestination: null });
const rep = (subId: string, breached: boolean, at: string): MlmfReport => ({ reportId: `${subId}-${at}`, subscriptionId: subId, metrics: {}, breachedFloor: breached, reportedAt: at });

describe("stage board", () => {
  // Each pipeline state belongs to exactly one of the five columns, and an ACTIVE runtime wins over the model state.
  it("puts each lifecycle state in its column", () => {
    expect(stageOf("REGISTERED", "NOT_DEPLOYED")).toBe("registered");
    expect(stageOf("TRAINED", "NOT_DEPLOYED")).toBe("training");
    expect(stageOf("PENDING_APPROVAL", "NOT_DEPLOYED")).toBe("validating");
    expect(stageOf("CERTIFIED", "DEPLOYED")).toBe("promoted");
    expect(stageOf("PROMOTED", "ACTIVE")).toBe("active");
    expect(stageOf("DEPRECATED", "ACTIVE")).toBe("active");
    expect(stageOf("RETIRED", "ACTIVE")).toBeNull();
    expect(stageOf("FAILED", "NOT_DEPLOYED")).toBeNull();
  });

  // Only the newest report of a subscription decides the outline: a model that recovered must not stay red.
  it("outlines a model only when its newest report breached", () => {
    const subs = [sub("s1", "a"), sub("s2", "b")];
    const reports = [rep("s1", false, "3"), rep("s2", true, "3"), rep("s1", true, "2")];
    expect([...breachedModels(subs, reports)]).toEqual(["b"]);
  });

  // A model AIMgF never touched is REGISTERED (as AIMgF defaults it); failed and ended models are counted off the board, not dropped silently.
  it("builds the columns and the off-board counts", () => {
    const board = buildBoard([model("a"), model("b"), model("c"), model("d")], [life("b", "PROMOTED", "ACTIVE"), life("c", "FAILED"), life("d", "TRAINING")], new Set(["b"]));
    const ids = Object.fromEntries(board.columns.map((c) => [c.id, c.cards.map((x) => x.modelId)]));
    expect(ids).toEqual({ registered: ["a"], training: ["d"], validating: [], promoted: [], active: ["b"] });
    expect(board.columns.find((c) => c.id === "active")!.cards[0].breach).toBe(true);
    expect(board.off).toEqual({ FAILED: 1 });
  });

  // GUI-10.6: the versions come only from the location MLMR writes on upload (`model-artifact:<model id>:<latest>`), counting up from 1; an
  // absent location, an external URI (whose last ":" part may be a port) or another model's location holds none
  it("reads the artifact versions from MLMR's own location pattern only", () => {
    const id = "0b9f3f1e-1111-4222-8333-444455556666";
    expect(artifactVersions(`model-artifact:${id}:3`)).toEqual([3, 2, 1]);
    expect(artifactVersions(`model-artifact:${id}:3`, id.toUpperCase())).toEqual([3, 2, 1]);
    expect(artifactVersions(`model-artifact:${id}:3`, "another-model")).toEqual([]);
    expect(artifactVersions(null)).toEqual([]);
    expect(artifactVersions("s3://bucket/m:3")).toEqual([]);
    expect(artifactVersions("http://models.example:8080")).toEqual([]);
    expect(artifactVersions(`model-artifact:${id}:0`)).toEqual([]);
    expect(artifactVersions(`model-artifact:${id}:latest`)).toEqual([]);
  });

  // Roll back is only legal from PROMOTED, deprecate from CERTIFIED or PROMOTED, retire from DEPRECATED or FAILED (aimgf statemachine).
  it("offers only the end-of-life events the state allows", () => {
    expect(endOfLifeEvents("PROMOTED").map((e) => e.event)).toEqual(["ROLLBACK", "DEPRECATE"]);
    expect(endOfLifeEvents("CERTIFIED").map((e) => e.event)).toEqual(["DEPRECATE"]);
    expect(endOfLifeEvents("FAILED").map((e) => e.event)).toEqual(["RETIRE"]);
    expect(endOfLifeEvents("TRAINING")).toEqual([]);
  });

  // The server's counts by state land in their columns; models AIMgF has no row for count as Registered; end-of-life states are off the board.
  it("turns the server's state counts into column counts", () => {
    const out = stageCounts([{ state: "TRAINING", count: 4 }, { state: "TRAINED", count: 1 }, { state: "PROMOTED", count: 2 }, { state: "RETIRED", count: 3 }], 15);
    expect(out.columns).toEqual({ registered: 5, training: 5, validating: 0, promoted: 2 });
    expect(out.off).toEqual({ RETIRED: 3 });
    expect(stageCounts([], null).columns.registered).toBe(0);
  });
});
