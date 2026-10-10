/** Software page (route /software, BRIEF §4e feature 2, handoff `Software.dc.html`): RAN software campaigns in waves with an alarm gate.
 * Tabs (hash): Campaigns (tiles, the paged list, the selected campaign's detail with its controls and events, the elements of one wave, and
 * who is told when a campaign halts or a rollback fails),
 * Element jobs (every per-element software job, flow 19) and New campaign (dry run, then start). Layout only: the boxes are `sections/`, their
 * data `data/queries.ts`; the selected campaign, wave and state filter live in the URL. Sections and limits: README.md. */
import { PageHeader, Tabs, useHashTab } from "../../components/ui";
import { count, useSummary } from "../../data/summary";
import { SectionBoundary } from "../../kit/SectionBoundary";
import { useSelectedCampaign } from "./data/queries";
import { CampaignDetail } from "./sections/CampaignDetail";
import { CampaignElements } from "./sections/CampaignElements";
import { CampaignList } from "./sections/CampaignList";
import { CampaignTiles } from "./sections/CampaignTiles";
import { ElementJobs } from "./sections/ElementJobs";
import { LifecycleWatchers } from "./sections/LifecycleWatchers";
import { NewCampaignForm } from "./sections/NewCampaignForm";
import "./software.css";

const TABS = ["campaigns", "jobs", "new"] as const;

/** The Software page. */
export function Software() {
  const [tab, setTab] = useHashTab(TABS, "campaigns");
  const [, select] = useSelectedCampaign();
  const summary = useSummary("software");
  return (
    <>
      <PageHeader eyebrow="RAN NF OAM · software campaigns · O-RAN WG4 software management" title="Software"
        subtitle="Upgrade the RAN in waves. Each wave is gated on new alarms; a failed gate halts or rolls back the campaign, as you choose."
        actions={<button type="button" className="btn primary" onClick={() => setTab("new")}>New campaign</button>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "campaigns", label: "Campaigns", count: count(summary.data, "campaigns.total") },
        { id: "jobs", label: "Element jobs" },
        { id: "new", label: "New campaign" },
      ]} />
      {tab === "campaigns" && <>
        <SectionBoundary id="software.tiles"><CampaignTiles /></SectionBoundary>
        <div className="grid g-side-main">
          <SectionBoundary id="software.list"><CampaignList /></SectionBoundary>
          <div className="stack">
            <SectionBoundary id="software.detail"><CampaignDetail /></SectionBoundary>
            <SectionBoundary id="software.elements"><CampaignElements /></SectionBoundary>
          </div>
        </div>
        <SectionBoundary id="software.watchers"><LifecycleWatchers section="software.watchers" /></SectionBoundary>
      </>}
      {tab === "jobs" && <SectionBoundary id="software.jobs"><ElementJobs /></SectionBoundary>}
      {tab === "new" && <SectionBoundary id="software.new"><NewCampaignForm onStarted={(id) => { select(id); setTab("campaigns"); }} /></SectionBoundary>}
    </>
  );
}
