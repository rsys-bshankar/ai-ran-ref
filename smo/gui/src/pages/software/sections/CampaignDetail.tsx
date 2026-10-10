/** Software · campaign detail (`software.detail`): the selected campaign (`GET /software-campaigns/{id}`) and its report (`/report`): what it
 * upgrades and how (selector, wave size, pause, gate, on gate failure), why it halted (GATE_FAILED, WAVE_PAUSE with a countdown, OPERATOR_HALT),
 * the controls, the totals, the wave strip (a wave opens its elements below) and the event log, newest first. */
import { Card, Id, StateBadge } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Callout } from "../../../kit/Callout";
import { Empty, ErrorRetry, Skeleton } from "../../../kit/states";
import { Timeline } from "../../../kit/Timeline";
import { describeSeconds, formatTime } from "../../../lib/domain";
import { useCampaign, useCampaignReport, useSelectedCampaign, useSelectedWave } from "../data/queries";
import { HALTED_MEANING, waveState, type Campaign, type CampaignReport } from "../data/types";
import { CampaignActions } from "./CampaignActions";
import { Countdown } from "./Countdown";

/** The detail card of the selected campaign; a wave button opens that wave's elements below. */
export function CampaignDetail() {
  const [id] = useSelectedCampaign();
  const [wave, onWave] = useSelectedWave();
  const campaign = useCampaign(id);
  const report = useCampaignReport(id);
  if (!id) return <Card section="software.detail" title="Campaign"><Empty title="Pick a campaign to see its waves.">Or start one on the New campaign tab.</Empty></Card>;
  if (campaign.error && !campaign.data) return <Card section="software.detail" title="Campaign"><ErrorRetry error={campaign.error} onRetry={() => void campaign.refetch()} /></Card>;
  const c = campaign.data;
  if (!c) return <Card section="software.detail" title="Campaign"><Skeleton lines={8} /></Card>;
  return (
    <Card section="software.detail" title={c.name} sub={<span className="row wrap"><Id value={c.campaignId} /><StateBadge state={c.status} />
      <Badge tone="mute" plain>on gate failure: {c.onGateFailure}</Badge>{c.softwareVersion && <span className="mono small">→ {c.softwareVersion}</span>}</span>}>
      <p className="small muted">{describeCampaign(c)}</p>
      <Halted c={c} />
      {c.status !== "HALTED" && <CampaignActions campaign={c} />}
      {report.error && !report.data && <ErrorRetry error={report.error} onRetry={() => void report.refetch()} />}
      {report.data && <Totals r={report.data} />}
      {report.data && <>
        <div className="eyebrow">Waves · {Math.min(c.wave, c.waveCount)} of {c.waveCount} started</div>
        <div className="sw-waves" role="group" aria-label="Waves">
          {report.data.waves.map((w) => {
            const state = waveState(w, c);
            return (
              <button key={w.wave} type="button" className={`sw-wave ${state.replace(" ", "-")}${wave === w.wave ? " on" : ""}`} aria-pressed={wave === w.wave} onClick={() => onWave(w.wave)}>
                <span className="mono xs">W{w.wave}</span><span className="small">{state}</span><span className="xs">{w.elements.length} element(s)</span>
              </button>
            );
          })}
        </div>
      </>}
      <div className="eyebrow">Events</div>
      {c.events.length === 0 ? <p className="muted small">No event yet.</p> : (
        <Timeline label="Campaign events" items={[...c.events].reverse().slice(0, 50).map((e, i) => ({
          key: `${e.at}-${i}`, state: e.event === "HALTED" || e.event.includes("FAIL") ? "fail" : "done",
          title: `${e.event}${e.wave ? ` · wave ${e.wave}` : ""}`,
          meta: <span className="xs muted">{formatTime(e.at)}{e.by ? ` · by ${e.by}` : ""}</span>, detail: e.detail ?? undefined,
        }))} />
      )}
    </Card>
  );
}

/** "selector vendorName=…, region=… · 812 elements · wave size 100 · pause 15 min · gate: ≤ 0 new critical/major alarms · requested by …". */
function describeCampaign(c: Campaign): string {
  const sel = c.selector ? Object.entries(c.selector).filter(([, v]) => v).map(([k, v]) => `${k}=${v}`).join(", ") : null;
  return [sel ? `selector ${sel}` : "named elements", `${c.elements.length} element(s)`, `wave size ${c.waveSize}`,
    c.wavePauseSeconds ? `pause ${describeSeconds(c.wavePauseSeconds)}` : "no pause", `gate: ≤ ${c.gateMaxNewAlarms} new critical/major alarms`,
    `requested by ${c.requestedBy}`].join(" · ");
}

/** The halted callout: reason, its meaning, the detail, the pause countdown and the controls. */
function Halted({ c }: { c: Campaign }) {
  if (c.status !== "HALTED") return null;
  const reason = c.haltedReason ?? "HALTED";
  return (
    <Callout tone={reason === "WAVE_PAUSE" ? "warn" : "bad"} title={`Halted after wave ${c.wave} · ${reason}`}
      actions={<CampaignActions campaign={c} />}>
      <span>{HALTED_MEANING[reason] ?? "Halted"}{c.haltedDetail ? ` — ${c.haltedDetail}` : ""}.</span>
      {reason === "WAVE_PAUSE" && c.nextWaveAt && <span> Wave {c.wave + 1} starts in <Countdown until={c.nextWaveAt} /> unless you act.</span>}
      {c.wave < c.waveCount && reason !== "WAVE_PAUSE" && <span> Waves {c.wave + 1}–{c.waveCount} not started.</span>}
    </Callout>
  );
}

/** The five totals of the report. */
function Totals({ r }: { r: CampaignReport }) {
  const box = (label: string, value: number, tone?: string) => (
    <div className="inset" key={label}><div className="xs muted">{label}</div><div className={`num${tone ? ` ${tone}` : ""}`}>{value.toLocaleString("en-US")}</div></div>
  );
  return (
    <>
      <div className="sw-insets">
        {box("Elements", r.summary.elements)}{box("Started", r.summary.started)}{box("Completed", r.summary.completed, "t-ok")}
        {box("Failed", r.summary.failed, r.summary.failed ? "t-bad" : undefined)}{box("Not reached", r.summary.notReached)}
        {r.summary.reverted > 0 && box("Reverted", r.summary.reverted, "t-warn")}
      </div>
      {r.attention.length > 0 && (
        <Callout tone="warn" title={`${r.attention.length} element(s) need attention`}>
          {r.attention.slice(0, 5).map((a) => `${a.managedElementRef}: ${a.problem}`).join(" · ")}{r.attention.length > 5 ? ` · and ${r.attention.length - 5} more` : ""}
        </Callout>
      )}
    </>
  );
}
