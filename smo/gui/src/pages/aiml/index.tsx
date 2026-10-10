/** The AI/ML page (route /aiml, BRIEF §4 AI/ML, handoff `Aiml.dc.html`, SCALE.md "AI/ML"): model registration → training → validation →
 * certification → deployment → inference, per `docs/call-flows/02-aiml-model-train-to-inference.md`. Layout only: the tabs (kept in the URL hash,
 * pre-redesign ids still work) and the sections each tab places; every section owns its data through `data/queries.ts`, and only the visible
 * tab's sections mount, so only its calls run. Sections and budgets: README.md. `Mlmf` is also the KPIs page's "Model KPIs (MLMF)" tab. */
import { useState } from "react";

import { Can, PageHeader, Tabs, useHashTab } from "../../components/ui";
import { count } from "../../data/summary";
import { SectionBoundary } from "../../kit/SectionBoundary";
import { Segmented } from "../../kit/Segmented";
import { Empty } from "../../kit/states";
import { MODELS, useAimlSummary } from "./data/queries";
import { CoordinationGroups } from "./sections/CoordinationGroups";
import { FeatureGroups } from "./sections/FeatureGroups";
import { Governance } from "./sections/Governance";
import { GuardKpiChart } from "./sections/GuardKpiChart";
import { InferenceJobs } from "./sections/InferenceJobs";
import { Mlmf } from "./sections/Mlmf";
import { ModelArtifacts } from "./sections/ModelArtifacts";
import { ModelDetail } from "./sections/ModelDetail";
import { RegisterModel } from "./sections/ModelForms";
import { ModelRuntime } from "./sections/ModelRuntime";
import { ModelTable } from "./sections/ModelTable";
import { ArtifactVersions, Repositories, Storages } from "./sections/Registry";
import { TrainingNow, WaitingForGovernance } from "./sections/SideCards";
import { StageBoard } from "./sections/StageBoard";
import { TrainingJobs } from "./sections/TrainingJobs";
import { ScopeNote } from "../../kit/ScopeNote";

export { Mlmf } from "./sections/Mlmf";
export { useModelNames } from "./data/queries";

const TABS = ["models", "training", "inference", "features", "groups", "mlmf", "registry"] as const;

/** The page. */
export function Aiml() {
  const [tab, setTab] = useHashTab(TABS, "models");
  const summary = useAimlSummary();
  const breaches = count(summary.data, "mlmfBreaches.total");
  const [registering, setRegistering] = useState(false);
  return (
    <>
      <PageHeader eyebrow="MLMR · AIMgF · MLLF · MLMF" title="AI/ML"
        subtitle={<>Model registration → training → validation → certification → deployment → inference, per <code>docs/call-flows/02-aiml-model-train-to-inference.md</code> <ScopeNote summary={summary.data} keys={["models", "trainingJobs", "mlmfBreaches"]} /></>}
        actions={<Can method="POST" path={MODELS}><button type="button" className="btn primary" onClick={() => setRegistering(true)}>Register model</button></Can>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "models", label: "Models", count: count(summary.data, "models.total") },
        { id: "training", label: "Training jobs", count: count(summary.data, "trainingJobs.total") },
        { id: "inference", label: "Inference jobs" }, { id: "features", label: "Feature groups" },
        { id: "groups", label: "Coordination groups" },
        { id: "mlmf", label: "Performance monitoring (MLMF)", count: breaches || null, tone: breaches ? "bad" : undefined },
        { id: "registry", label: "Registry" },
      ]} />
      {tab === "models" && <ModelsTab onTraining={() => setTab("training")} />}
      {tab === "training" && <SectionBoundary id="aiml.training"><TrainingJobs /></SectionBoundary>}
      {tab === "inference" && <SectionBoundary id="aiml.inference"><InferenceJobs /></SectionBoundary>}
      {tab === "features" && <SectionBoundary id="aiml.features"><FeatureGroups /></SectionBoundary>}
      {tab === "groups" && <SectionBoundary id="aiml.groups"><CoordinationGroups /></SectionBoundary>}
      {tab === "mlmf" && <SectionBoundary id="aiml.mlmf"><Mlmf /></SectionBoundary>}
      {tab === "registry" && (
        <div className="stack">
          <div className="grid g2">
            <SectionBoundary id="aiml.repositories"><Repositories /></SectionBoundary>
            <SectionBoundary id="aiml.storages"><Storages /></SectionBoundary>
          </div>
          <SectionBoundary id="aiml.artifactVersions"><ArtifactVersions /></SectionBoundary>
        </div>
      )}
      {registering && <RegisterModel onClose={() => setRegistering(false)} />}
    </>
  );
}

/** The Models tab: the board (or the table) across the top, the selected model's sections beside the two side cards. */
function ModelsTab({ onTraining }: { onTraining: () => void }) {
  const [view, setView] = useState<"board" | "table">("board");
  const [selected, setSelected] = useState<string | null>(null);
  const viewSwitch = <Segmented label="View" value={view} onChange={setView} options={[{ id: "board", label: "Board" }, { id: "table", label: "Table" }]} />;
  return (
    <div className="stack">
      {view === "board"
        ? <SectionBoundary id="aiml.board"><StageBoard selected={selected} onSelect={setSelected} actions={viewSwitch} /></SectionBoundary>
        : <SectionBoundary id="aiml.table"><ModelTable selected={selected} onSelect={setSelected} actions={viewSwitch} /></SectionBoundary>}
      <div className="grid g-main-side">
        <div className="stack">
          {selected ? <>
            <SectionBoundary id="aiml.detail"><ModelDetail id={selected} onClose={() => setSelected(null)} /></SectionBoundary>
            <SectionBoundary id="aiml.guard"><GuardKpiChart modelId={selected} /></SectionBoundary>
            <SectionBoundary id="aiml.governance"><Governance id={selected} /></SectionBoundary>
            <SectionBoundary id="aiml.artifacts"><ModelArtifacts id={selected} /></SectionBoundary>
            <SectionBoundary id="aiml.runtime"><ModelRuntime id={selected} /></SectionBoundary>
          </> : <section className="card"><Empty title="Select a model">Pick a card on the board, or a row in the table, to see its pipeline, guard KPI and history.</Empty></section>}
        </div>
        <div className="stack">
          <SectionBoundary id="aiml.trainingTop"><TrainingNow onSelect={setSelected} onSeeAll={onTraining} /></SectionBoundary>
          <SectionBoundary id="aiml.waiting"><WaitingForGovernance onSelect={setSelected} /></SectionBoundary>
        </div>
      </div>
    </div>
  );
}
