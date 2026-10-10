/** SMO platform health (`dashboard.platform`, anchor `#health`): one tile per SMO module (up or down, latency or error) and the readiness /
 * version / build table (PR-OBS-8.2). Read from `GET /api/modules/status`, the BFF's check of every module's /health, /ready and /version
 * through R1 Termination. The header carries "Modules healthy n/m", which scripts/gui_smoke.py reads from the page text. */
import { Card, DataTable } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { ErrorRetry, Skeleton } from "../../../kit/states";
import { moduleRows } from "../../../lib/domain";
import { useModulesStatus } from "../data/queries";

/** The card. */
export function PlatformHealth() {
  const status = useModulesStatus();
  const modules = status.data?.modules ?? [];
  const healthy = modules.filter((m) => m.healthy).length;
  return (
    <Card section="dashboard.platform" className="s2 anchor" title="SMO platform health"
      sub={status.data ? `Health checked ${new Date(status.data.checkedAt).toLocaleTimeString()} · refreshes every 10 s` : "GET /<module>/health, /ready and /version via R1 Termination"}
      actions={status.data && <Badge tone={healthy === modules.length ? "ok" : "bad"}>Modules healthy {healthy}/{modules.length}</Badge>}>
      <div id="health" />
      {status.error && !status.data && <ErrorRetry error={status.error} onRetry={() => void status.refetch()} />}
      {!status.data && !status.error && <Skeleton lines={3} />}
      <div className="health-grid">
        {modules.map((m) => (
          <div key={m.module} className={`health-tile ${m.healthy ? "up" : "down"}`} title={m.error ?? `HTTP ${m.statusCode}`}>
            <span className="dot" />
            <div><strong>{m.module}</strong><span className="muted small">{m.healthy ? `up · ${m.latencyMs} ms` : `down · ${m.error ?? `HTTP ${m.statusCode}`}`}</span></div>
          </div>
        ))}
      </div>
      {status.data && (
        <DataTable rows={moduleRows(status.data.modules)} rowKey={(r) => r.module} columns={[
          { header: "Module", render: (r) => <strong>{r.module}</strong> },
          { header: "Readiness", render: (r) => <Badge tone={r.readiness === "READY" ? "ok" : r.readiness === "UNKNOWN" ? "mute" : "bad"}>{r.readiness}</Badge> },
          { header: "Version", render: (r) => r.version },
          { header: "Build", render: (r) => <><code>{r.buildSha}</code>{r.skewed && <Badge tone="warn" title="A different commit than most modules">differs</Badge>}</> },
          { header: "Built at", render: (r) => r.builtAt },
        ]} />
      )}
    </Card>
  );
}
