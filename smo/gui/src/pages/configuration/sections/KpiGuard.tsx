/** Configuration · KPI guard of a job (part of `configuration.job`): the guard the job declared (KPI, baseline and observation windows, allowed
 * regression, which way is better, whether a regression is rolled back) and what the worker's check found, per element. "Run KPI check"
 * (`POST /config-jobs/{id}/kpi-check`) is not exposed by the GUI BFF (gui-bff/app/rbac.py has no rule for it), so the guard is read-only
 * here and the worker's own run is the only check (README, Known limits). */
import type { ConfigJob } from "../../../api/types";
import { DataTable, Id } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { describeGuardResult, formatTime } from "../../../lib/domain";

/** The guard of `job`, or a line saying it has none. */
export function KpiGuard({ job }: { job: ConfigJob }) {
  const g = job.kpiGuard;
  if (!g) return <p className="muted small">No KPI guard: nothing checks a KPI after this job.</p>;
  const r = job.kpiGuardResult;
  const tone = r?.verdict === "OK" ? "ok" : r?.verdict === "REGRESSED" ? "bad" : r ? "warn" : "mute";
  const box = (label: string, value: string, sub?: string) => (
    <div className="inset"><div className="xs muted">{label}</div><div className="mono small">{value}</div>{sub && <div className="xs muted">{sub}</div>}</div>
  );
  return (
    <>
      <div className="grid g4">
        {box("KPI", g.kpi, `${g.direction} is better`)}
        {box("Baseline · observe", `${g.baselineMinutes} min · ${g.observationMinutes} min`)}
        {box("Max regression", `${g.maxRegressionPercent} %`, g.revert ? "regressed elements are rolled back" : "report only")}
        <div className="inset"><div className="xs muted">Last check</div><Badge tone={tone}>{r?.verdict ?? "NOT CHECKED"}</Badge>
          <div className="xs muted">{describeGuardResult(r, job.kpiGuardCheckedAt)}{r?.checkedAt ? ` · ${formatTime(r.checkedAt)}` : ""}</div></div>
      </div>
      {r?.revertJobId && <p className="small">Rollback job <Id value={r.revertJobId} /></p>}
      {r?.elements && r.elements.length > 0 && (
        <DataTable rows={r.elements.slice(0, 50)} rowKey={(e) => e.managedElementRef} columns={[
          { header: "Element", render: (e) => e.managedElementRef },
          { header: "Baseline", render: (e) => e.baseline ?? "—" },
          { header: "Observed", render: (e) => e.observed ?? "—" },
          { header: "Change", render: (e) => (e.changePercent === null ? "—" : `${e.changePercent.toFixed(1)} %`) },
          { header: "Verdict", render: (e) => <Badge tone={e.verdict === "OK" ? "ok" : e.verdict === "REGRESSED" ? "bad" : "warn"}>{e.verdict}</Badge> },
        ]} />
      )}
      <p className="gap-note">Run KPI check: the GUI BFF does not expose this call yet; the worker checks the guard once the observation window has passed.</p>
    </>
  );
}
