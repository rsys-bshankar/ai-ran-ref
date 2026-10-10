/** The Data & Exposure page (route /data, BRIEF §4 and §4b, handoff `Data.dc.html`, SCALE.md "Data & Exposure"): DME and SME / CAPIF — call
 * flows 01 and 08 — in three hash tabs: Flow & jobs (`#dme`, the default and the pre-redesign DME tab id), Producers & offers (`#offers`) and
 * SME (`#sme`). Layout only: each tab mounts its own sections, so only the visible tab's queries run. Sections and limits: README.md. */
import { PageHeader, Tabs, useHashTab } from "../../components/ui";
import { SectionBoundary } from "../../kit/SectionBoundary";
import "./data.css";
import { DataFlow } from "./sections/DataFlow";
import { DataJobs } from "./sections/DataJobs";
import { Discovery, EventSubscriptions } from "./sections/Discovery";
import { ExposedServices } from "./sections/ExposedServices";
import { Invokers } from "./sections/Invokers";
import { Offers, TypeSubscriptions } from "./sections/Offers";
import { DataTypes, Producers } from "./sections/Producers";

const TABS = ["dme", "offers", "sme"] as const;

/** The page. */
export function Data() {
  const [tab, setTab] = useHashTab(TABS, "dme");
  return (
    <>
      <PageHeader eyebrow="DME · SME (CAPIF)" title="Data & Exposure"
        subtitle={<>Who produces which data, who consumes it, and which services rApps are allowed to call — Data Management & Exposure (DME) and Service Management & Exposure (SME / CAPIF), call flows 01 and 08</>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "dme", label: "DME: flow & jobs" }, { id: "offers", label: "DME: producers & offers" }, { id: "sme", label: "SME: services & invokers" },
      ]} />
      {tab === "dme" && <div className="stack">
        <SectionBoundary id="data.flow"><DataFlow /></SectionBoundary>
        <SectionBoundary id="data.jobs"><DataJobs /></SectionBoundary>
      </div>}
      {tab === "offers" && <div className="stack">
        <SectionBoundary id="data.producers"><Producers /></SectionBoundary>
        <SectionBoundary id="data.types"><DataTypes /></SectionBoundary>
        <SectionBoundary id="data.offers"><Offers /></SectionBoundary>
        <SectionBoundary id="data.type-subscriptions"><TypeSubscriptions /></SectionBoundary>
      </div>}
      {tab === "sme" && <div className="stack">
        <div className="grid g2" style={{ alignItems: "start" }}>
          <div className="stack"><SectionBoundary id="data.providers"><ExposedServices /></SectionBoundary></div>
          <SectionBoundary id="data.invokers"><Invokers /></SectionBoundary>
        </div>
        <SectionBoundary id="data.discovery"><Discovery /></SectionBoundary>
        <SectionBoundary id="data.capif-subscriptions"><EventSubscriptions /></SectionBoundary>
      </div>}
    </>
  );
}
