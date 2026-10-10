/** Software · elements of a wave (`software.elements`): the elements of the selected campaign's chosen wave from its report, with each one's
 * software job (phase, status, "timed out" when the job timeout failed it, whether a revert undid it). The job opens its flow-19 board (`/flows/19?subject=<job>`), the element its
 * Element detail page. Elements of a wave that has not started have no job yet. Paged 50 at a time (a wave is at most `waveSize` elements). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Card, DataTable, Id, StateBadge } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Pager } from "../../../kit/Pager";
import { Empty } from "../../../kit/states";
import { elementHref } from "../../element/data/types";
import { useCampaignReport, useSelectedCampaign, useSelectedWave } from "../data/queries";
import { flow19Href, type WaveJob } from "../data/types";

const PAGE = 50;

/** One row: an element and its job, when the wave has started. */
interface Row { me: string; job: WaveJob | null }

/** The card for the selected wave of the selected campaign. */
export function CampaignElements() {
  const [id] = useSelectedCampaign();
  const [wave] = useSelectedWave();
  const report = useCampaignReport(id);
  const [offset, setOffset] = useState(0);
  useEffect(() => { setOffset(0); }, [id, wave]);
  if (!id) return null;
  const w = report.data?.waves.find((x) => x.wave === wave) ?? report.data?.waves[0];
  const jobs = new Map((w?.jobs ?? []).map((j) => [j.managedElementRef, j]));
  const rows: Row[] = (w?.elements ?? []).map((me) => ({ me, job: jobs.get(me) ?? null }));
  return (
    <Card section="software.elements" title={w ? `Elements of wave ${w.wave}` : "Elements"} sub="one software job each (flow 19)">
      {!report.data && !report.error && <p className="muted small">Loading…</p>}
      {report.data && !w && <Empty title="This campaign has no waves." />}
      {w && (
        <>
          <DataTable<Row> rows={rows.slice(offset, offset + PAGE)} rowKey={(r) => r.me} empty="No element in this wave." columns={[
            { header: "Element", render: (r) => <Link to={elementHref(r.me)}>{r.me}</Link> },
            { header: "Job", render: (r) => (r.job ? <Link to={flow19Href(r.job.jobId)} title="Open its flow-19 board"><Id value={r.job.jobId} /></Link> : <span className="muted">not started</span>) },
            { header: "Phase", render: (r) => r.job?.phase ?? "—" },
            { header: "Status", render: (r) => (r.job ? <span className="row wrap"><StateBadge state={r.job.status} />{r.job.timedOut && <Badge tone="bad" title="The element did not report in time (the campaign's job timeout)">timed out</Badge>}</span> : <span className="muted">—</span>) },
            { header: "Revert", render: (r) => (r.job?.revert ? <StateBadge state={r.job.revert} /> : <span className="muted">—</span>) },
          ]} />
          <Pager offset={offset} limit={PAGE} shown={rows.slice(offset, offset + PAGE).length} total={rows.length} onOffset={setOffset} />
        </>
      )}
    </Card>
  );
}
