/** Section `kpis.chart` (Overview, GUI-4.1 to 4.3): the chosen KPIs over the Overview's range, one line chart each, computed by RAN NF OAM per
 * step (`GET /ran-nf-oam/kpis/{name}/series`, each step's ratio from its summed counters). The region and site cluster narrow the charts (GUI-4.2);
 * they start at the top bar's scope and follow it when it changes, and can be set here for the charts alone. The KPIs, the range and the place can
 * be saved as a named layout of the user's (GUI-4.3, `/api/me/kpi-layouts` in the BFF, so a layout follows the user to any browser) and loaded
 * again. A step without data is left out of the line, and the caption says how many were. */
import { useEffect, useState } from "react";

import { useSmo } from "../../../api/hooks";
import { LineChart } from "../../../components/charts";
import { Card } from "../../../components/ui";
import { useScope, type Scope } from "../../../data/scope";
import { Empty, ErrorRetry, Skeleton } from "../../../kit/states";
import { pickable, SCOPES_PATH, type ScopesAnswer } from "../../../shell/ScopePicker";
import { MAX_CHARTED, useKpiDefinitions, useKpiLayouts, useKpiLayoutWrite, useKpiSeries, type Range } from "../data/queries";

/** What the chart shows before the user picks anything: the first tile's KPI. */
export const DEFAULT_CHARTED = ["dl_ue_throughput"];
/** The shape of a layout name the BFF accepts (gui-bff/app/kpi_layouts.py LAYOUT_NAME). */
const LAYOUT_NAME = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}$/;

/** The box: its controls (layout, KPIs, place) and one chart per KPI. `range` is the Overview's, so a loaded layout sets it through `onRange`. */
export function KpiChart({ range, onRange }: { range: Range; onRange: (r: Range) => void }) {
  const scope = useScope();
  const [kpis, setKpis] = useState<string[]>(DEFAULT_CHARTED);
  const [place, setPlace] = useState<Scope>(scope);
  const [layout, setLayout] = useState("");
  const [saveAs, setSaveAs] = useState("");
  useEffect(() => { setPlace({ region: scope.region, cluster: scope.cluster }); }, [scope.region, scope.cluster]);   // the top bar's scope changed
  const defs = useKpiDefinitions();
  const scopes = useSmo<ScopesAnswer>(SCOPES_PATH, undefined, { staleTime: 60_000 });
  const layouts = useKpiLayouts();
  const write = useKpiLayoutWrite();
  const regions = scopes.data?.regions ?? [];
  const addable = (defs.data ?? []).map((d) => d.name).filter((n) => !kpis.includes(n));
  const load = (name: string) => {
    setLayout(name);
    const saved = layouts.data?.items.find((l) => l.name === name);
    if (!saved) return;
    setKpis(saved.kpis);
    onRange(saved.range);
    setPlace({ region: saved.region, cluster: saved.region ? saved.siteCluster : null });
  };
  const save = () => {
    const name = saveAs.trim();
    write.mutate({ name, layout: { kpis, range, region: place.region, siteCluster: place.region ? place.cluster : null } },
      { onSuccess: () => { setLayout(name); setSaveAs(""); } });
  };
  return (
    <Card section="kpis.chart" title="KPIs over time" sub={`${range}, ${place.region ? (place.cluster ? `${place.region} / ${place.cluster}` : place.region) : "all network"}`}
      actions={
        <div className="row gap wrap">
          <select aria-label="Layout" value={layout} onChange={(e) => load(e.target.value)}>
            <option value="">{layouts.data?.items.length ? "Saved layouts…" : "No saved layout"}</option>
            {(layouts.data?.items ?? []).map((l) => <option key={l.name} value={l.name}>{l.name}</option>)}
          </select>
          {layout && <button type="button" className="btn ghost small" disabled={write.isPending}
            onClick={() => write.mutate({ name: layout, layout: null }, { onSuccess: () => setLayout("") })}>Delete layout</button>}
          <input aria-label="Layout name" placeholder="Save as…" value={saveAs} maxLength={60} onChange={(e) => setSaveAs(e.target.value)} />
          <button type="button" className="btn small" disabled={!LAYOUT_NAME.test(saveAs.trim()) || kpis.length === 0 || write.isPending} onClick={save}
            title="Saves the KPIs, the range and the region under this name, for you">Save layout</button>
        </div>
      }>
      <div className="row gap wrap">
        <select aria-label="Add KPI" value="" disabled={kpis.length >= MAX_CHARTED || addable.length === 0}
          onChange={(e) => { if (e.target.value) setKpis([...kpis, e.target.value]); }}>
          <option value="">{kpis.length >= MAX_CHARTED ? `At most ${MAX_CHARTED} KPIs` : "Add KPI…"}</option>
          {addable.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <select aria-label="Chart region" value={place.region ?? ""} onChange={(e) => setPlace({ region: e.target.value || null, cluster: null })}>
          <option value="">All network</option>
          {regions.filter((r) => pickable(r.region)).map((r) => <option key={r.region} value={r.region!}>{r.region}</option>)}
          {place.region && !regions.some((r) => r.region === place.region) && <option value={place.region}>{place.region}</option>}
        </select>
        {place.region && (
          <select aria-label="Chart site cluster" value={place.cluster ?? ""} onChange={(e) => setPlace({ region: place.region, cluster: e.target.value || null })}>
            <option value="">All of {place.region}</option>
            {(regions.find((r) => r.region === place.region)?.siteClusters ?? []).filter((c) => pickable(c.siteCluster))
              .map((c) => <option key={c.siteCluster} value={c.siteCluster!}>{c.siteCluster}</option>)}
            {place.cluster && <option value={place.cluster} hidden>{place.cluster}</option>}
          </select>
        )}
      </div>
      {kpis.length === 0 ? <Empty title="No KPI charted.">Add one with "Add KPI…".</Empty>
        : <div className="grid g2">{kpis.map((name) => <KpiSeriesChart key={name} name={name} range={range} place={place}
          onRemove={() => setKpis(kpis.filter((k) => k !== name))} />)}</div>}
    </Card>
  );
}

/** One KPI's chart, with a button that takes it off. A KPI that is not defined (404) says so instead of failing the box. */
function KpiSeriesChart({ name, range, place, onRemove }: { name: string; range: Range; place: Scope; onRemove: () => void }) {
  const q = useKpiSeries(name, range, place);
  const points = (q.data?.points ?? []).filter((p) => p.value !== null).map((p) => ({ t: p.at, v: p.value as number }));
  const gaps = (q.data?.points.length ?? 0) - points.length;
  return (
    <div className="inset" data-kpi={name}>
      <div className="row between">
        <strong className="small">{name}</strong>
        <button type="button" className="btn ghost small" aria-label={`Remove ${name}`} onClick={onRemove}>×</button>
      </div>
      {q.error ? (q.error.status === 404 ? <span className="muted small">not defined (Definitions tab)</span> : <ErrorRetry error={q.error} onRetry={() => void q.refetch()} />)
        : !q.data ? <Skeleton lines={3} />
          : points.length === 0 ? <span className="muted small">no data in window</span>
            : <>
              <LineChart points={points} label={name} unit={q.data.unit ?? undefined} height={140} />
              <span className="xs muted">{q.data.points.length} steps of {Math.round(q.data.stepSeconds / 60)} min{gaps ? `, ${gaps} without data` : ""}
                {q.data.truncated ? " · the server capped the computation" : ""}</span>
            </>}
    </div>
  );
}
