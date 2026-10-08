// The generic renderer of the operator page a rApp declares in its package (PR-GUI-8, GUI-8.4; docs/adr/0004-operator-ui-declaration.md; the format and its
// limits are smo_shared/operator_ui.py, the reading of it is lib/operatorUi.ts).
//
//   <DeclaredPage>   the panels, top to bottom: table, keyValues, kpis, chart, actions. A kind this build does not know is a card saying "unsupported panel";
//                    a panel that throws while drawing is a card saying it could not be drawn. The others are drawn either way.
//   rowDetail        a click on a table row opens a drawer of blocks: json, keyValues, table, chart. An unknown block is "unsupported block".
//
// Everything on the page is text: values and titles are React text nodes, never HTML, a link or an attribute that means something (a badge's colour comes from the
// state words the GUI already knows, not from the value). The browser calls only the BFF (`/api/rapps/<instance>/operator/...`), which allows exactly the routes
// the declaration lists; a change button is drawn only when the BFF says the user may change (`canChange`).

import { Component, useState, type ErrorInfo, type FormEvent, type ReactNode } from "react";

import { useOperatorAction, useOperatorRead, type Declaration } from "../api/rapps";
import { useSmo } from "../api/hooks";
import { ApiError } from "../api/client";
import type { PerfReport } from "../api/types";
import {
  asText, fillQuery, fillRoute, fillTitle, formatNumber, formatValue, getPath, isObj, isTime, listAt, numberOf, readInputs, sparkPoints, toSeries, whenMatches,
  type InputSpec, type Obj, type Series,
} from "../lib/operatorUi";
import { formatTime } from "../lib/domain";
import { Sparkline } from "./charts";
import { Card, DataTable, Drawer, ErrorBox, Field, Id, Json, KeyValue, Modal, StateBadge } from "./ui";

const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Obj => (isObj(v) ? v : {});
const str = (v: unknown): string => (typeof v === "string" ? v : asText(v));

// ---------------------------------------------------------------- the page

export function DeclaredPage({ instanceId, declaration, canChange, operatorApiRegistered }: {
  instanceId: string; declaration: Declaration; canChange: boolean; operatorApiRegistered: boolean;
}) {
  return (
    <>
      {!operatorApiRegistered && (
        <Card><p className="muted">This rApp's operator API is not registered, so the panels that read from it cannot be shown. The rApp registers it when it starts
          (<code>PUT /rapp-mgmt/instances/&lt;id&gt;/operator-api</code>), or an operator sets it on the instance.</p></Card>
      )}
      {declaration.panels.map((panel, i) => (
        <PanelBoundary key={`${str(panel.id)}-${i}`} title={str(panel.title)}>
          <PanelView instanceId={instanceId} panel={panel} canChange={canChange} registered={operatorApiRegistered} />
        </PanelBoundary>
      ))}
    </>
  );
}

class PanelBoundary extends Component<{ title: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.warn("operator page panel failed to draw", error.message, info.componentStack); }
  render() {
    return this.state.failed ? <Card title={this.props.title}><p className="muted">This panel could not be drawn.</p></Card> : this.props.children;
  }
}

function PanelView({ instanceId, panel, canChange, registered }: { instanceId: string; panel: Obj; canChange: boolean; registered: boolean }) {
  const common = { instanceId, panel, registered };
  switch (panel.kind) {
    case "table": return <TablePanel {...common} canChange={canChange} />;
    case "keyValues": return <KeyValuesPanel {...common} />;
    case "kpis": return <KpisPanel {...common} />;
    case "chart": return <ChartPanel {...common} />;
    case "actions": return <ActionsPanel {...common} canChange={canChange} />;
    default: return <Unsupported title={str(panel.title)} what="panel" />;
  }
}

function Unsupported({ title, what }: { title: string; what: "panel" | "block" }) {
  return (
    <Card title={title}>
      <p className="muted">unsupported {what}</p>
    </Card>
  );
}

// ---------------------------------------------------------------- reading a source

interface Source { path?: unknown; query?: unknown; refreshSeconds?: unknown }

/** A declared read, with the route filled (`row` binds `{row.<field>}` for a rowDetail block). Not sent when the rApp has no registered operator API. */
function useSource(instanceId: string, source: unknown, enabled: boolean, row?: Obj) {
  const s = obj(source) as Source;
  const route = typeof s.path === "string" ? fillRoute(s.path, instanceId, row) : null;
  const query = fillQuery(isObj(s.query) ? s.query : undefined, row);
  const read = useOperatorRead(instanceId, route, query, s.refreshSeconds, enabled && typeof s.path === "string");
  return { read, valid: route !== null && query !== null };
}

function SourceState({ read, valid, registered = true, children }: {
  read: ReturnType<typeof useOperatorRead>; valid: boolean; registered?: boolean; children: (data: unknown) => ReactNode;
}) {
  if (!registered) return <p className="muted small">Available once the rApp registers its operator API.</p>;
  if (!valid) return <p className="muted small">The route of this panel cannot be built from the values it names.</p>;
  if (read.error) {
    const err = read.error;
    if (err instanceof ApiError && err.title === "OPERATOR_API_NOT_REGISTERED") return <p className="muted small">This rApp's operator API is not registered (or the instance is terminated).</p>;
    return <ErrorBox error={err} />;
  }
  if (read.data === undefined) return <p className="muted small">Loading…</p>;
  return <>{children(read.data)}</>;
}

function RefreshButton({ read }: { read: ReturnType<typeof useOperatorRead> }) {
  return <button className="btn ghost small" onClick={() => read.refetch()} disabled={read.isFetching} aria-label="Refresh this panel">{read.isFetching ? "…" : "↻"}</button>;
}

// ---------------------------------------------------------------- values

const FORMATS_WITH_TEXT = new Set(["text", "number", "percent", "datetime", "boolean", "list"]);

function CellValue({ value, format, unit }: { value: unknown; format?: unknown; unit?: unknown }) {
  const f = typeof format === "string" ? format : "text";
  const u = typeof unit === "string" ? unit : undefined;
  if (value === undefined || value === null || value === "") return <span className="muted">—</span>;
  if (f === "badge") return <StateBadge state={asText(value)} />;
  if (f === "id") return typeof value === "string" || typeof value === "number" ? <Id value={String(value)} /> : <>{asText(value)}</>;
  return <>{FORMATS_WITH_TEXT.has(f) || f === "sparkline" ? formatValue(value, f === "sparkline" ? "text" : f, u) : formatValue(value, "text", u)}</>;
}

interface Column { path: string; label: string; format?: string; unit?: string; y?: string }

/** The columns when every one has the shape this build knows; otherwise null and the panel is "unsupported". */
function readColumns(value: unknown): Column[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const out: Column[] = [];
  for (const c of value) {
    if (!isObj(c) || typeof c.path !== "string" || typeof c.label !== "string") return null;
    out.push({ path: c.path, label: c.label, format: typeof c.format === "string" ? c.format : undefined, unit: typeof c.unit === "string" ? c.unit : undefined, y: typeof c.y === "string" ? c.y : undefined });
  }
  return out;
}

function columnCell(row: unknown, c: Column): ReactNode {
  const value = getPath(row, c.path);
  if (c.format === "sparkline" && c.y) {
    const points = sparkPoints(value, c.y);
    return <Sparkline points={points} width={160} height={36} label={c.label} />;
  }
  return <CellValue value={value} format={c.format} unit={c.unit} />;
}

interface Keyed { row: Obj; key: string }

function keyRows(rows: unknown[], rowKey: string | undefined): Keyed[] {
  const seen = new Set<string>();
  return rows.map((r, i) => {
    const row = obj(r);
    let key = rowKey ? asText(getPath(row, rowKey)) : String(i);
    if (seen.has(key)) key = `${key}#${i}`;
    seen.add(key);
    return { row, key };
  });
}

// ---------------------------------------------------------------- table

function TablePanel({ instanceId, panel, canChange, registered }: { instanceId: string; panel: Obj; canChange: boolean; registered: boolean }) {
  const { read, valid } = useSource(instanceId, panel.source, registered);
  const [open, setOpen] = useState<Keyed | null>(null);
  const columns = readColumns(panel.columns);
  if (!columns) return <Unsupported title={str(panel.title)} what="panel" />;
  const rowActions = arr(panel.rowActions).filter(isObj);
  const detail = isObj(panel.rowDetail) && Array.isArray(panel.rowDetail.blocks) ? panel.rowDetail : null;
  return (
    <Card title={str(panel.title)} actions={<RefreshButton read={read} />}>
      <SourceState read={read} valid={valid} registered={registered}>
        {(data) => {
          const rows = keyRows(listAt(data, typeof panel.rows === "string" ? panel.rows : undefined) ?? [], typeof panel.rowKey === "string" ? panel.rowKey : undefined);
          return (
            <DataTable rows={rows} rowKey={(r) => r.key} empty={typeof panel.empty === "string" ? panel.empty : "Nothing here."}
              onRowClick={detail ? setOpen : undefined} selectedKey={open?.key}
              columns={[
                ...columns.map((c) => ({ header: c.label, render: (r: Keyed) => columnCell(r.row, c) })),
                ...(canChange && rowActions.length > 0
                  ? [{ header: "", className: "actions", render: (r: Keyed) => (
                    <div className="row gap end">
                      {rowActions.filter((a) => whenMatches(a.when, r.row)).map((a) => <DeclaredButton key={str(a.id)} instanceId={instanceId} action={a} row={r.row} />)}
                    </div>) }]
                  : []),
              ]} />
          );
        }}
      </SourceState>
      {open && detail && <RowDrawer instanceId={instanceId} detail={detail} row={open.row} onClose={() => setOpen(null)} />}
    </Card>
  );
}

// ---------------------------------------------------------------- the drawer of a row

function RowDrawer({ instanceId, detail, row, onClose }: { instanceId: string; detail: Obj; row: Obj; onClose: () => void }) {
  const title = typeof detail.title === "string" ? fillTitle(detail.title, row) : "Details";
  return (
    <Drawer title={title} onClose={onClose}>
      {arr(detail.blocks).map((b, i) => <BlockView key={i} instanceId={instanceId} block={obj(b)} row={row} />)}
    </Drawer>
  );
}

function BlockView({ instanceId, block, row }: { instanceId: string; block: Obj; row: Obj }) {
  const title = str(block.title);
  const body = (() => {
    switch (block.kind) {
      case "json": return <JsonBlock block={block} row={row} />;
      case "keyValues": return <KeyValuesBlock block={block} row={row} />;
      case "table": return <TableBlock instanceId={instanceId} block={block} row={row} />;
      case "chart": return <ChartBlock instanceId={instanceId} block={block} row={row} />;
      default: return null;
    }
  })();
  return (
    <section>
      <h3>{title}</h3>
      <PanelBoundary title={title}>{body ?? <p className="muted">unsupported block</p>}</PanelBoundary>
    </section>
  );
}

function JsonBlock({ block, row }: { block: Obj; row: Obj }) {
  const value = typeof block.path === "string" ? getPath(row, block.path) : row;
  if (value === undefined || value === null) return <p className="muted">{typeof block.empty === "string" ? block.empty : "—"}</p>;
  return <Json value={value} />;
}

function KeyValuesBlock({ block, row }: { block: Obj; row: Obj }) {
  return <ItemsList items={arr(block.items)} data={row} />;
}

/** A block's list: a field of the row, or (a `source`) a list fetched for this row when the drawer opens. */
function useBlockData(instanceId: string, block: Obj, row: Obj) {
  const fetched = isObj(block.source);
  const { read, valid } = useSource(instanceId, block.source, fetched, row);
  return { fetched, read, valid };
}

function TableBlock({ instanceId, block, row }: { instanceId: string; block: Obj; row: Obj }) {
  const { fetched, read, valid } = useBlockData(instanceId, block, row);
  const columns = readColumns(block.columns);
  if (!columns) return <p className="muted">unsupported block</p>;
  const rowsPath = typeof block.rows === "string" ? block.rows : undefined;
  const draw = (answer: unknown) => (
    <DataTable rows={keyRows(listAt(answer, rowsPath) ?? [], undefined)} rowKey={(r) => r.key} empty={typeof block.empty === "string" ? block.empty : "Nothing here."}
      columns={columns.map((c) => ({ header: c.label, render: (r: Keyed) => columnCell(r.row, c) }))} />
  );
  return fetched ? <SourceState read={read} valid={valid}>{draw}</SourceState> : draw(row);
}

function ChartBlock({ instanceId, block, row }: { instanceId: string; block: Obj; row: Obj }) {
  const { fetched, read, valid } = useBlockData(instanceId, block, row);
  if (!chartSpecOk(block)) return <p className="muted">unsupported block</p>;
  return fetched ? <SourceState read={read} valid={valid}>{(data) => <ChartFrom spec={block} data={data} />}</SourceState> : <ChartFrom spec={block} data={row} />;
}

// ---------------------------------------------------------------- key values

function ItemsList({ items, data }: { items: unknown[]; data: unknown }) {
  const rows: [ReactNode, ReactNode][] = [];
  for (const it of items) {
    if (!isObj(it) || typeof it.path !== "string") continue;
    rows.push([str(it.label), <CellValue key={it.path} value={getPath(data, it.path)} format={it.format} unit={it.unit} />]);
  }
  return rows.length ? <KeyValue items={rows} /> : <p className="muted">—</p>;
}

function KeyValuesPanel({ instanceId, panel, registered }: { instanceId: string; panel: Obj; registered: boolean }) {
  const { read, valid } = useSource(instanceId, panel.source, registered);
  if (!Array.isArray(panel.items) || panel.items.some((i) => !isObj(i) || typeof i.path !== "string")) return <Unsupported title={str(panel.title)} what="panel" />;
  return (
    <Card title={str(panel.title)} actions={<RefreshButton read={read} />}>
      <SourceState read={read} valid={valid} registered={registered}>{(data) => <ItemsList items={panel.items as unknown[]} data={data} />}</SourceState>
    </Card>
  );
}

// ---------------------------------------------------------------- KPI tiles

function KpisPanel({ instanceId, panel, registered }: { instanceId: string; panel: Obj; registered: boolean }) {
  const tiles = arr(panel.tiles);
  const needsSource = tiles.some((t) => isObj(t) && typeof t.path === "string");
  const needsKpi = tiles.some((t) => isObj(t) && typeof t.kpi === "string");
  const { read, valid } = useSource(instanceId, panel.source, registered && needsSource);
  const perf = useSmo<PerfReport[]>(`/rapp-mgmt/instances/${instanceId}/performance`, { limit: 1 }, { enabled: needsKpi });
  if (tiles.length === 0 || tiles.some((t) => !isObj(t) || typeof t.label !== "string" || (typeof t.path !== "string") === (typeof t.kpi !== "string"))) {
    return <Unsupported title={str(panel.title)} what="panel" />;
  }
  const latest = perf.data?.[0]?.metrics;
  const grid = (data: unknown) => (
    <div className="grid cols-4 tight">
      {tiles.map((t, i) => {
        const tile = obj(t);
        const raw = typeof tile.path === "string" ? getPath(data, tile.path) : isObj(latest) ? getPath(latest, str(tile.kpi)) : undefined;
        const n = numberOf(raw);
        return (
          <div className="stat" key={i}>
            <span className="stat-label">{str(tile.label)}</span>
            <span className="stat-value">{n === null ? "—" : formatValue(n, typeof tile.format === "string" ? tile.format : "number", typeof tile.unit === "string" ? tile.unit : undefined)}</span>
          </div>
        );
      })}
    </div>
  );
  return (
    <Card title={str(panel.title)} actions={needsSource ? <RefreshButton read={read} /> : undefined}>
      {needsSource
        ? <SourceState read={read} valid={valid} registered={registered}>{grid}</SourceState>
        : perf.error ? <ErrorBox error={perf.error} /> : grid(undefined)}
    </Card>
  );
}

// ---------------------------------------------------------------- charts

type ChartSpec = Obj & { type: "line" | "bar"; points: string; x: string; y: string };
const chartSpecOk = (spec: Obj): spec is ChartSpec => (spec.type === "line" || spec.type === "bar") && typeof spec.points === "string" && typeof spec.x === "string" && typeof spec.y === "string";

function ChartPanel({ instanceId, panel, registered }: { instanceId: string; panel: Obj; registered: boolean }) {
  const { read, valid } = useSource(instanceId, panel.source, registered);
  if (!chartSpecOk(panel)) return <Unsupported title={str(panel.title)} what="panel" />;
  return (
    <Card title={str(panel.title)} actions={<RefreshButton read={read} />}>
      <SourceState read={read} valid={valid} registered={registered}>{(data) => <ChartFrom spec={panel} data={data} />}</SourceState>
    </Card>
  );
}

function ChartFrom({ spec, data }: { spec: Obj; data: unknown }) {
  if (!chartSpecOk(spec)) return null;
  const { series, extra } = toSeries(listAt(data, spec.points), spec.x, spec.y, typeof spec.seriesBy === "string" ? spec.seriesBy : undefined);
  return <SeriesChart type={spec.type as "line" | "bar"} series={series} extra={extra} unit={typeof spec.unit === "string" ? spec.unit : undefined} label={str(spec.title)} />;
}

const W = 560, H = 200, PAD = { l: 48, r: 10, t: 10, b: 26 };
const MAX_DRAWN = 500;

export function SeriesChart({ type, series, extra = 0, unit, label }: { type: "line" | "bar"; series: Series[]; extra?: number; unit?: string; label?: string }) {
  const drawn = series.map((s) => ({ ...s, points: s.points.slice(-MAX_DRAWN) })).filter((s) => s.points.length > 0);
  if (drawn.length === 0) return <p className="muted">no data</p>;
  const xs: string[] = [];
  for (const s of drawn) for (const p of s.points) if (!xs.includes(p.x)) xs.push(p.x);
  const ys = drawn.flatMap((s) => s.points.map((p) => p.y));
  const lo = type === "bar" ? Math.min(0, ...ys) : Math.min(...ys);
  const hi = Math.max(...ys);
  const span = hi - lo || 1;
  const x = (i: number) => (xs.length === 1 ? (PAD.l + W - PAD.r) / 2 : PAD.l + (i * (W - PAD.l - PAD.r)) / (xs.length - 1));
  const y = (v: number) => H - PAD.b - ((v - lo) * (H - PAD.t - PAD.b)) / span;
  const xLabel = (v: string) => (isTime(v) ? formatTime(v) : v);
  const barWidth = Math.max(2, Math.min(24, (W - PAD.l - PAD.r) / Math.max(1, xs.length * drawn.length) - 1));
  const ticks = [lo, lo + span / 2, hi];
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label ?? "chart"}: ${drawn.map((s) => s.name || "series").join(", ")}`}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} className="chart-grid" />
            <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end" className="chart-axis">{formatNumber(t)}</text>
          </g>
        ))}
        <text x={PAD.l} y={H - 8} className="chart-axis">{xLabel(xs[0])}</text>
        {xs.length > 1 && <text x={W - PAD.r} y={H - 8} textAnchor="end" className="chart-axis">{xLabel(xs[xs.length - 1])}</text>}
        {drawn.map((s, si) => type === "line" ? (
          <g key={si} className={`chart-s${si}`}>
            <path className="chart-line" fill="none" d={s.points.map((p, i) => `${i === 0 ? "M" : "L"}${x(xs.indexOf(p.x)).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ")} />
            {s.points.length <= 60 && s.points.map((p, i) => <circle key={i} className="chart-dot" r={2.2} cx={x(xs.indexOf(p.x))} cy={y(p.y)}><title>{`${s.name ? `${s.name}: ` : ""}${formatNumber(p.y)}${unit ? ` ${unit}` : ""} @ ${xLabel(p.x)}`}</title></circle>)}
          </g>
        ) : (
          <g key={si} className={`chart-s${si}`}>
            {s.points.map((p, i) => {
              const cx = x(xs.indexOf(p.x)) + (si - (drawn.length - 1) / 2) * (barWidth + 1);
              const top = y(Math.max(p.y, 0)), bottom = y(Math.min(p.y, 0));
              return <rect key={i} className="chart-bar" x={cx - barWidth / 2} width={barWidth} y={top} height={Math.max(1, bottom - top)}><title>{`${s.name ? `${s.name}: ` : ""}${formatNumber(p.y)}${unit ? ` ${unit}` : ""} @ ${xLabel(p.x)}`}</title></rect>;
            })}
          </g>
        ))}
      </svg>
      <figcaption className="small muted">
        {unit && <span>{unit} </span>}
        {drawn.length > 1 && drawn.map((s, i) => <span key={i} className={`chart-key chart-s${i}`}><span className="chart-swatch" />{s.name || "—"} </span>)}
        {extra > 0 && <span>+{extra} more series not drawn</span>}
      </figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------- actions

function ActionsPanel({ instanceId, panel, canChange }: { instanceId: string; panel: Obj; canChange: boolean; registered: boolean }) {
  const actions = arr(panel.actions).filter(isObj);
  if (actions.length === 0) return <Unsupported title={str(panel.title)} what="panel" />;
  return (
    <Card title={str(panel.title)}>
      {canChange
        ? <div className="row gap">{actions.map((a) => <DeclaredButton key={str(a.id)} instanceId={instanceId} action={a} />)}</div>
        : <p className="muted small">Changing needs the operator role{actions.length ? ` (${actions.map((a) => str(a.label)).join(", ")})` : ""}.</p>}
    </Card>
  );
}

/** A declared button. With `inputs` it first asks for them; with `confirm` it then asks to confirm; `{user}` in the fixed body is filled by the BFF, not here. */
function DeclaredButton({ instanceId, action, row }: { instanceId: string; action: Obj; row?: Obj }) {
  const mutation = useOperatorAction(instanceId);
  const [asking, setAsking] = useState(false);
  const path = typeof action.path === "string" ? fillRoute(action.path, instanceId, row) : null;
  const method = action.method;
  if (path === null || typeof action.id !== "string" || (method !== "POST" && method !== "PUT" && method !== "PATCH" && method !== "DELETE")) return null;
  const inputs = arr(action.inputs).filter(isObj) as unknown as InputSpec[];
  const send = (body: Obj) => {
    if (typeof action.confirm === "string" && !window.confirm(action.confirm)) return;
    mutation.mutate({ actionId: action.id as string, method, path, body, success: str(action.success) }, { onSuccess: () => setAsking(false) });
  };
  const tone = action.tone === "primary" || action.tone === "danger" ? action.tone : "default";
  return (
    <>
      <button className={`btn ${tone}`} disabled={mutation.isPending} onClick={(e) => { e.stopPropagation(); if (inputs.length) setAsking(true); else send({}); }}>
        {mutation.isPending ? "…" : str(action.label)}
      </button>
      {asking && <InputsDialog title={str(action.label)} inputs={inputs} pending={mutation.isPending} onClose={() => setAsking(false)} onSend={send} />}
    </>
  );
}

function InputsDialog({ title, inputs, pending, onClose, onSend }: { title: string; inputs: InputSpec[]; pending: boolean; onClose: () => void; onSend: (body: Obj) => void }) {
  const [values, setValues] = useState<Record<string, string | boolean | undefined>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const set = (name: string, value: string | boolean) => setValues((v) => ({ ...v, [name]: value }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const out = readInputs(inputs, values);
    setErrors(out.errors);
    if (Object.keys(out.errors).length === 0) onSend(out.body);
  };
  return (
    <Modal title={title} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {inputs.map((spec) => (
          <Field key={spec.name} label={str(spec.label)} hint={errors[spec.name] ? <span className="text-bad">{errors[spec.name]}</span> : undefined}>
            {spec.type === "boolean"
              ? <input type="checkbox" checked={values[spec.name] === true} onChange={(e) => set(spec.name, e.target.checked)} />
              : spec.type === "enum"
                ? <select value={String(values[spec.name] ?? "")} onChange={(e) => set(spec.name, e.target.value)}><option value="">Choose…</option>{(spec.options ?? []).map((o) => <option key={o}>{o}</option>)}</select>
                : <input type={spec.type === "string" ? "text" : "number"} step={spec.type === "integer" ? 1 : "any"} min={spec.min} max={spec.max} maxLength={spec.maxLength}
                    value={String(values[spec.name] ?? "")} onChange={(e) => set(spec.name, e.target.value)} />}
          </Field>
        ))}
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={pending}>Send</button></div>
      </form>
    </Modal>
  );
}
