/** The network health map (`dashboard.map`, handoff `Main.dc.html`, BRIEF §4b): regions as tiles, a region opens its site clusters, a site
 * cluster opens its managed elements (server-paged, each linking to the element page); the crumb goes back up. Every tile is coloured by the
 * worst open alarm severity of its elements and shows how many are unhealthy (an open critical or major alarm), all computed by RAN NF OAM
 * (`GET /managed-entities/health?group_by=region|site_cluster`, ran-nf-oam/app/fleet.py). Elements with no region are counted but cannot be
 * opened (no route filters "no region"); a region's elements with no site cluster open as the region's whole element list, labelled so. */
import { useState } from "react";
import { Link } from "react-router-dom";

import { Card } from "../../../components/ui";
import { formatCount } from "../../../kit/Kpi";
import { Icon } from "../../../kit/icons";
import { ServerTable } from "../../../kit/ServerTable";
import { QueryState } from "../../../kit/states";
import { ELEMENTS_PATH, useFleetHealth } from "../data/queries";
import type { HealthGroup, ManagedEntity, WorstSeverity } from "../data/types";

/** The tile tone of a group's worst open severity: critical → bad, major → warn, anything else → ok. */
export function toneOf(sev: WorstSeverity): "bad" | "warn" | "ok" {
  return sev === "critical" ? "bad" : sev === "major" ? "warn" : "ok";
}

/** True when a health answer has no group (no element at this level). */
const noGroups = (d: unknown) => ((d as { groups?: unknown[] }).groups ?? []).length === 0;

/** Where the map is: all regions, one region's clusters, or one cluster's (or one region's) elements. */
type Level = { region: null } | { region: string; cluster?: undefined } | { region: string; cluster: string | null };

/** One tile of the map. */
function GroupTile({ g, kind, onOpen }: { g: HealthGroup; kind: string; onOpen?: () => void }) {
  const label = g.key ?? `no ${kind}`;
  const body = (
    <>
      <span className="eyebrow">{kind}</span>
      <b>{label}</b>
      <span className="small">{formatCount(g.elements)} elements · <span className={g.unhealthy ? "t-bad" : "muted"}>{formatCount(g.unhealthy)} unhealthy</span></span>
      <span className="xs muted">{g.worstSeverity ? `worst open: ${g.worstSeverity}` : "no open alarm"}</span>
    </>
  );
  const cls = `tile ${toneOf(g.worstSeverity)}`;
  return onOpen
    ? <button type="button" className={cls} onClick={onOpen} aria-label={`Open ${kind} ${label}`}>{body}</button>
    : <div className={cls} title={`Elements without a ${kind} cannot be listed by ${kind}`}>{body}</div>;
}

/** The map card. */
export function HealthMap() {
  const [level, setLevel] = useState<Level>({ region: null });
  const regions = useFleetHealth("region");
  const inRegion = level.region !== null;
  const clusters = useFleetHealth("site_cluster", level.region, inRegion);
  const atElements = inRegion && "cluster" in level && level.cluster !== undefined;
  const cluster = atElements ? (level as { cluster: string | null }).cluster : undefined;
  const score = regions.data?.healthScore;
  return (
    <Card section="dashboard.map" className="s2" title="Network health map"
      sub={`${formatCount(regions.data?.groups.reduce((a, g) => a + g.elements, 0) ?? null)} managed elements · health score ${score == null ? "—" : `${score} %`} · regions → site clusters → elements`}
      actions={<Link className="btn small" to="/topology">Open topology</Link>}>
      <nav className="crumb" aria-label="Map level">
        {inRegion ? <button type="button" className="btn ghost small" onClick={() => setLevel({ region: null })}>All regions</button> : <strong>All regions</strong>}
        {inRegion && <><span aria-hidden>›</span>{atElements
          ? <button type="button" className="btn ghost small" onClick={() => setLevel({ region: level.region })}>{level.region}</button>
          : <strong>{level.region}</strong>}</>}
        {atElements && <><span aria-hidden>›</span><strong>{cluster ?? "all elements"}</strong></>}
        {!inRegion && <span>· click a region to open its site clusters</span>}
      </nav>
      {atElements ? (
        <>
          <ServerTable<ManagedEntity> path={ELEMENTS_PATH} query={{ region: level.region, site_cluster: cluster ?? undefined }}
            rowKey={(e) => `${e.managedElementRef}|${e.managedFunctionRef ?? ""}`} empty="No elements here." columns={[
              { header: "Element", render: (e) => <Link to={`/elements/${encodeURIComponent(e.managedElementRef)}`}>{e.managedElementRef}</Link> },
              { header: "Function", render: (e) => e.managedFunctionRef ? <code className="small">{e.managedFunctionRef}</code> : <span className="muted">—</span> },
              { header: "Site cluster", render: (e) => e.siteCluster ?? <span className="muted">—</span> },
              { header: "Type", render: (e) => e.entityType ?? "—" },
              { header: "Vendor", render: (e) => e.vendorName ?? "—" },
            ]} />
          <div className="row between">
            <button type="button" className="btn small" onClick={() => setLevel({ region: level.region })}><Icon name="back" size={14} />Back to {level.region}</button>
            <Link className="small" to="/alarms">Open the alarm console →</Link>
          </div>
        </>
      ) : inRegion ? (
        <QueryState q={clusters} isEmpty={noGroups} empty={<p className="muted">No elements in this region.</p>}>
          {clusters.data && (
            <>
              <div className="tiles">
                {clusters.data.groups.map((g) => <GroupTile key={g.key ?? "∅"} g={g} kind="site cluster" onOpen={() => setLevel({ region: level.region, cluster: g.key })} />)}
              </div>
              {clusters.data.groups.some((g) => g.key === null) && <p className="small muted">"no site cluster" opens every element of {level.region}; an admin sets a cluster on the element page.</p>}
            </>
          )}
        </QueryState>
      ) : (
        <QueryState q={regions} isEmpty={noGroups}
          empty={<p className="muted">No managed elements yet. A region is set with PUT /managed-entities/&#123;me&#125;/scope.</p>}>
          {regions.data && (
            <div className="tiles">
              {regions.data.groups.map((g) => <GroupTile key={g.key ?? "∅"} g={g} kind="region" onOpen={g.key === null ? undefined : () => setLevel({ region: g.key! })} />)}
            </div>
          )}
        </QueryState>
      )}
    </Card>
  );
}
