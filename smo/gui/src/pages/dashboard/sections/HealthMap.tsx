/** The network health map (`dashboard.map`, handoff `Main.dc.html`): regions as tiles, click one to drill into its managed elements (server-paged,
 * each linking to the element page), Back returns to the regions (BRIEF §4b). What the backend serves limits it (BRIEF §5):
 * - there is no region list route, so the region names come from one page of managed elements; counts per tile are shown only when that page
 *   held the whole fleet (otherwise a count would be a guess);
 * - elements carry a region but no site cluster, so the region → cluster level is skipped;
 * - alarms carry no region, so tiles are not coloured by worst state. */
import { useState } from "react";
import { Link } from "react-router-dom";

import { Card } from "../../../components/ui";
import { count } from "../../../data/summary";
import { formatCount } from "../../../kit/Kpi";
import { Icon } from "../../../kit/icons";
import { ServerTable } from "../../../kit/ServerTable";
import { QueryState } from "../../../kit/states";
import { ELEMENTS_PATH, REGION_SAMPLE, useDashboardSummary, useElementSample } from "../data/queries";
import type { ManagedEntity } from "../data/types";

/** The regions found on a page of elements, with the number of distinct elements in each, sorted by name; `unset` counts elements with no region. */
export function regionsOf(rows: ManagedEntity[]): { regions: { name: string; elements: number }[]; unset: number } {
  const by = new Map<string, Set<string>>();
  const unset = new Set<string>();
  for (const r of rows) {
    if (!r.region) { unset.add(r.managedElementRef); continue; }
    if (!by.has(r.region)) by.set(r.region, new Set());
    by.get(r.region)!.add(r.managedElementRef);
  }
  return { regions: [...by.entries()].map(([name, s]) => ({ name, elements: s.size })).sort((a, b) => a.name.localeCompare(b.name)), unset: unset.size };
}

/** The map card: region tiles, or one region's elements. */
export function HealthMap() {
  const [region, setRegion] = useState<string | null>(null);
  const summary = useDashboardSummary();
  const sample = useElementSample();
  const total = count(summary.data, "elements.total");
  const complete = (sample.data?.length ?? 0) < REGION_SAMPLE;      // a short page is the whole list: its counts are true
  const found = regionsOf(sample.data ?? []);
  return (
    <Card section="dashboard.map" className="s2" title="Network health map"
      sub={`${formatCount(total)} managed elements · regions → elements`}
      actions={<Link className="btn small" to="/topology">Open topology</Link>}>
      <nav className="crumb" aria-label="Map level">
        {region ? <button type="button" className="btn ghost small" onClick={() => setRegion(null)}>All regions</button> : <strong>All regions</strong>}
        {region && <><span aria-hidden>›</span><strong>{region}</strong><span>· its managed elements</span></>}
        {!region && <span>· click a region to open its elements</span>}
      </nav>
      {region ? (
        <>
          <ServerTable<ManagedEntity> path={ELEMENTS_PATH} query={{ region }} rowKey={(e) => `${e.managedElementRef}|${e.managedFunctionRef ?? ""}`}
            empty="No elements in this region." columns={[
              { header: "Element", render: (e) => <Link to={`/elements/${encodeURIComponent(e.managedElementRef)}`}>{e.managedElementRef}</Link> },
              { header: "Function", render: (e) => e.managedFunctionRef ? <code className="small">{e.managedFunctionRef}</code> : <span className="muted">—</span> },
              { header: "Type", render: (e) => e.entityType ?? "—" },
              { header: "Vendor", render: (e) => e.vendorName ?? "—" },
              { header: "Tenant", render: (e) => e.tenant ?? "—" },
            ]} />
          <div className="row between">
            <button type="button" className="btn small" onClick={() => setRegion(null)}><Icon name="back" size={14} />Back to all regions</button>
            <Link className="small" to="/alarms">Open the alarm console →</Link>
          </div>
        </>
      ) : (
        <QueryState q={sample} isEmpty={() => found.regions.length === 0}
          empty={<p className="muted">No managed element has a region set{found.unset ? ` (${found.unset} without one)` : ""}. Set it with PUT /managed-entities/&#123;me&#125;/scope.</p>}>
          <div className="tiles">
            {found.regions.map((r) => (
              <button key={r.name} type="button" className="tile" onClick={() => setRegion(r.name)} aria-label={`Open region ${r.name}`}>
                <span className="eyebrow">region</span>
                <b>{r.name}</b>
                <span className="small muted">{complete ? `${formatCount(r.elements)} elements` : "open to count"}</span>
              </button>
            ))}
          </div>
          {complete && found.unset > 0 && <p className="small muted">{found.unset} elements have no region.</p>}
          {!complete && <p className="gap-note">Regions found among the first {REGION_SAMPLE} elements; a region list with counts is not served yet.</p>}
        </QueryState>
      )}
      <p className="gap-note">Not shown yet: site clusters (elements carry no cluster) and colour by worst state (alarms carry no region).</p>
    </Card>
  );
}
