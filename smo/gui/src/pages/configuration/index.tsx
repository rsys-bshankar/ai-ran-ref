/** Configuration page (route /configuration, BRIEF §4e features 3 and 11, handoff `Configuration.dc.html`): every write to the RAN, staged in
 * waves with an alarm gate and an optional KPI guard, and the data that makes a write possible. Tabs (hash): Config jobs (tiles, the paged job
 * list with halted first, the selected job's waves and controls), New job, Vendors & schemas, Endpoint trust (pinned SSH host keys) and Element
 * onboarding. Only the visible tab's boxes load. Layout only: the boxes are `sections/`, their data `data/queries.ts`. Sections: README.md. */
import { PageHeader, Tabs, useHashTab } from "../../components/ui";
import { count, useSummary } from "../../data/summary";
import { SectionBoundary } from "../../kit/SectionBoundary";
import { useSelectedJob } from "./data/queries";
import { CmSchemas } from "./sections/CmSchemas";
import { ConfigTiles } from "./sections/ConfigTiles";
import { ElementOnboarding } from "./sections/ElementOnboarding";
import { HostKeys } from "./sections/HostKeys";
import { JobList } from "./sections/JobList";
import { NewJobForm } from "./sections/NewJobForm";
import { StagedJob } from "./sections/StagedJob";
import { Vendors } from "./sections/Vendors";
import "./configuration.css";

const TABS = ["jobs", "new", "vendors", "trust", "onboarding"] as const;

/** The Configuration page. */
export function Configuration() {
  const [tab, setTab] = useHashTab(TABS, "jobs");
  const [, select] = useSelectedJob();
  const summary = useSummary("configuration");
  const halted = count(summary.data, "configJobs.HALTED");
  return (
    <>
      <PageHeader eyebrow="RAN NF OAM · config jobs · vendors · schemas · endpoint trust" title="Configuration"
        subtitle="Every write to the RAN, staged in waves with an alarm gate and an optional KPI guard. Pause, continue, stop or roll back while it runs."
        actions={<button type="button" className="btn primary" onClick={() => setTab("new")}>New config job</button>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "jobs", label: "Config jobs", count: halted || null, tone: halted ? "bad" : undefined },
        { id: "new", label: "New job" },
        { id: "vendors", label: "Vendors & schemas" },
        { id: "trust", label: "Endpoint trust" },
        { id: "onboarding", label: "Element onboarding" },
      ]} />
      {tab === "jobs" && <>
        <SectionBoundary id="configuration.tiles"><ConfigTiles /></SectionBoundary>
        <div className="grid g-side-main">
          <SectionBoundary id="configuration.jobs"><JobList /></SectionBoundary>
          <SectionBoundary id="configuration.job"><StagedJob /></SectionBoundary>
        </div>
      </>}
      {tab === "new" && <SectionBoundary id="configuration.new"><NewJobForm onSubmitted={(id) => { select(id); setTab("jobs"); }} /></SectionBoundary>}
      {tab === "vendors" && <div className="grid g2">
        <SectionBoundary id="configuration.vendors"><Vendors /></SectionBoundary>
        <SectionBoundary id="configuration.schemas"><CmSchemas /></SectionBoundary>
      </div>}
      {tab === "trust" && <SectionBoundary id="configuration.trust"><HostKeys /></SectionBoundary>}
      {tab === "onboarding" && <SectionBoundary id="configuration.onboarding"><ElementOnboarding /></SectionBoundary>}
    </>
  );
}
