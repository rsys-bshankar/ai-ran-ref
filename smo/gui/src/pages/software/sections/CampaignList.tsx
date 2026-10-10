/** Software · campaign list (`software.list`): every software campaign, paged by the server (`GET /software-campaigns?status=`, SCALE.md P1),
 * filtered by state (kept in `?status=`, also set by the running/halted tile). A row opens the campaign's detail (`?campaign=`). */
import { Card, StateBadge, type Column } from "../../../components/ui";
import { Meter } from "../../../kit/Meter";
import { ServerTable } from "../../../kit/ServerTable";
import { formatTime } from "../../../lib/domain";
import { useUrlParam } from "../../element/data/url";
import { CAMPAIGNS_PATH, useSelectedCampaign } from "../data/queries";
import { CAMPAIGN_STATES, type CampaignRow } from "../data/types";

/** The list's columns. */
const COLUMNS: Column<CampaignRow>[] = [
  { header: "Campaign", render: (c) => <strong>{c.name}</strong> },
  { header: "Version", render: (c) => <span className="mono small">{c.softwareVersion ?? "—"}</span> },
  { header: "Status", render: (c) => <span className="row wrap"><StateBadge state={c.status} />{c.haltedReason && <span className="small muted">{c.haltedReason}</span>}</span> },
  { header: "Waves", render: (c) => (
    <span className="col" style={{ gap: 4 }}>
      <span className="small">{Math.min(c.wave, c.waveCount)} of {c.waveCount}</span>
      <Meter parts={[{ key: "waves run", value: Math.min(c.wave, c.waveCount), tone: c.status === "HALTED" ? "warn" : "ok" }]} total={c.waveCount} />
    </span>
  ) },
  { header: "Created", render: (c) => <span className="small muted">{formatTime(c.createdAt)}</span> },
];

/** The list card. */
export function CampaignList() {
  const [status, setStatus] = useUrlParam("status");
  const [selected, select] = useSelectedCampaign();
  return (
    <Card section="software.list" title="Campaigns" sub="newest first · server-paged"
      actions={<label className="row small">State
        <select aria-label="Campaign state" value={status ?? ""} onChange={(e) => setStatus(e.target.value || null)}>
          <option value="">All</option>{CAMPAIGN_STATES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>}>
      <ServerTable<CampaignRow> path={CAMPAIGNS_PATH} query={status ? { status } : undefined} columns={COLUMNS} rowKey={(c) => c.campaignId}
        onRowClick={(c) => select(c.campaignId)} selectedKey={selected} empty={status ? `No ${status} campaign.` : "No software campaign yet."} />
    </Card>
  );
}
