/** The rApps page (route /rapps, BRIEF §4 and §4b, handoff `Rapps.dc.html`, SCALE.md "rApps · at scale"): summary tiles, the pinned &
 * attention strip, then four tabs — Directory (the default, with no hash), Instances, Packages and Rollouts — each loading only its own data.
 * Layout only: every box is a section under `sections/`, wrapped in a `SectionBoundary`. Sections and data: README.md. */
import { Can, PageHeader, Tabs, useHashTab } from "../../components/ui";
import { count } from "../../data/summary";
import { SectionBoundary } from "../../kit/SectionBoundary";
import { PACKAGES_PATH, useRappsSummary } from "./data/queries";
import { RappDirectory } from "./sections/Directory";
import { InstanceTable } from "./sections/InstanceTable";
import { OnboardForm } from "./sections/OnboardForm";
import { PackagesTable } from "./sections/PackagesTable";
import { PinnedAttention } from "./sections/PinnedAttention";
import { Rollouts } from "./sections/Rollouts";
import { SummaryTiles } from "./sections/SummaryTiles";

export { InstanceActions } from "./sections/InstanceActions";
export { VersionHistory } from "./sections/VersionHistory";

/** The tab ids (kept in the URL hash). `directory`, `packages` and `instances` are the pre-redesign ids, so old links keep working. */
const TABS = ["directory", "instances", "packages", "rollouts"] as const;

/** The page. */
export function Rapps() {
  const [tab, setTab] = useHashTab(TABS, "directory");
  const summary = useRappsSummary();
  const n = (k: string) => count(summary.data, k);
  return (
    <>
      <PageHeader eyebrow="Onboarding · rApp Management" title="rApps"
        subtitle={<>Every rApp and the page its package declares; Packages and Instances administer them (Onboarding → rApp Management → NFO, per <code>docs/call-flows/01-rapp-onboarding-to-deployment.md</code>)</>} />
      <div className="stack">
        <SectionBoundary id="rapps.tiles"><SummaryTiles /></SectionBoundary>
        <SectionBoundary id="rapps.pinned"><PinnedAttention /></SectionBoundary>
        <Tabs tabs={[
          { id: "directory", label: "Directory" },
          { id: "instances", label: "Instances", count: n("instances.total") },
          { id: "packages", label: "Packages", count: n("packages.total") },
          { id: "rollouts", label: "Rollouts", count: n("instances.UPGRADING") },
        ]} value={tab} onChange={setTab} />
        {tab === "directory" && <SectionBoundary id="rapps.directory"><RappDirectory /></SectionBoundary>}
        {tab === "instances" && <SectionBoundary id="rapps.instances"><InstanceTable /></SectionBoundary>}
        {tab === "packages" && <>
          <Can method="POST" path={PACKAGES_PATH}><SectionBoundary id="rapps.onboard"><OnboardForm /></SectionBoundary></Can>
          <SectionBoundary id="rapps.packages"><PackagesTable /></SectionBoundary>
        </>}
        {tab === "rollouts" && <SectionBoundary id="rapps.rollouts"><Rollouts /></SectionBoundary>}
      </div>
    </>
  );
}
