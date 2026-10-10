/** Data & Exposure → Flow & jobs, the data-flow box (`data.flow`): producers → data types → consumers as an SVG band chart, band width = job
 * count, at most 8 rows per column with "+N other" (`data/flow.ts`). Built from every DME type and one bounded page of jobs and offers; when a
 * page is cut the box says so. A "late" count is not shown: DME records no delivery time (BRIEF §5). */
import { useMemo } from "react";

import { Card } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { formatCount } from "../../../kit/Kpi";
import { Empty, QueryState } from "../../../kit/states";
import { buildFlow, type FlowLink, type FlowNode } from "../data/flow";
import { FLOW_LIMIT, useDmeTypes, useFlowJobs, useFlowOffers } from "../data/queries";

const W = 900;
const BOX = 210;
const ROW = 46;
const COLS = [0, (W - BOX) / 2, W - BOX];

/** The y centre of row `i`. */
const yOf = (i: number) => i * ROW + ROW / 2;

/** One column of rows. */
function Column({ nodes, x, title, unit }: { nodes: FlowNode[]; x: number; title: string; unit: (n: FlowNode) => string }) {
  return (
    <g>
      <text x={x} y={-10} className="flow-head">{title}</text>
      {nodes.map((n, i) => (
        <g key={n.key} className={`flow-node${n.folded ? " other" : ""}`}>
          <title>{`${n.label}: ${unit(n)}`}</title>
          <rect x={x} y={i * ROW + 4} width={BOX} height={ROW - 8} rx={8} />
          <text x={x + 10} y={yOf(i) + 4} className="flow-label">{n.label.length > 24 ? `${n.label.slice(0, 23)}…` : n.label}</text>
          <text x={x + BOX - 10} y={yOf(i) + 4} textAnchor="end" className="flow-count">{n.value.toLocaleString("en-US")}</text>
        </g>
      ))}
    </g>
  );
}

/** The bands between two columns; stroke width scales with the job count. */
function Bands({ links, from, to, x1, x2, max }: { links: FlowLink[]; from: FlowNode[]; to: FlowNode[]; x1: number; x2: number; max: number }) {
  const idx = (nodes: FlowNode[], k: string) => nodes.findIndex((n) => n.key === k);
  return (
    <g>
      {links.map((l) => {
        const a = idx(from, l.from);
        const b = idx(to, l.to);
        if (a < 0 || b < 0) return null;
        const mid = (x1 + x2) / 2;
        return (
          <path key={`${l.from}>${l.to}`} className="flow-band" strokeWidth={Math.max(2, (22 * l.value) / max)}
            d={`M${x1},${yOf(a)} C${mid},${yOf(a)} ${mid},${yOf(b)} ${x2},${yOf(b)}`}>
            <title>{`${from[a].label} → ${to[b].label}: ${l.value} jobs`}</title>
          </path>
        );
      })}
    </g>
  );
}

/** The box. */
export function DataFlow() {
  const types = useDmeTypes();
  const jobs = useFlowJobs();
  const offers = useFlowOffers();
  const flow = useMemo(() => (types.data && jobs.data ? buildFlow(types.data, jobs.data.items, offers.data?.items ?? []) : null), [types.data, jobs.data, offers.data]);
  const total = jobs.data?.total;
  const cut = jobs.data && (total !== undefined ? total > jobs.data.items.length : !!jobs.data.hasMore);
  const offersCut = offers.data && (offers.data.total !== undefined ? offers.data.total > offers.data.items.length : !!offers.data.hasMore);
  const rows = flow ? Math.max(flow.producers.length, flow.types.length, flow.consumers.length, 1) : 1;
  const max = flow ? Math.max(1, ...flow.left.map((l) => l.value), ...flow.right.map((l) => l.value)) : 1;
  return (
    <Card section="data.flow" title="Data flow" sub="producers → data types → consumers · width = jobs · smaller ones folded into “other”"
      actions={<><Badge tone="info">{formatCount(total ?? null)} data jobs</Badge><Badge tone="mute" title="DME records no delivery time, so lateness is not known">late: —</Badge></>}>
      <QueryState q={{ ...types, error: types.error ?? jobs.error, data: flow ?? undefined, refetch: () => { void types.refetch(); void jobs.refetch(); } }}
        isEmpty={() => !flow || (flow.types.length === 0 && flow.consumers.length === 0)}
        empty={<Empty title="No DME types or data jobs yet.">Register a producer type on the Producers & offers tab, then create a data job.</Empty>}>
        {flow && (
          <div className="flow-wrap">
            <svg viewBox={`-2 -30 ${W + 4} ${rows * ROW + 34}`} role="img"
              aria-label={`Data flow: ${flow.producers.length} producer rows, ${flow.types.length} type rows, ${flow.consumers.length} consumer rows`}>
              <Bands links={flow.left} from={flow.producers} to={flow.types} x1={COLS[0] + BOX} x2={COLS[1]} max={max} />
              <Bands links={flow.right} from={flow.types} to={flow.consumers} x1={COLS[1] + BOX} x2={COLS[2]} max={max} />
              <Column nodes={flow.producers} x={COLS[0]} title="PRODUCERS" unit={(n) => `${n.value} jobs on the types it supports`} />
              <Column nodes={flow.types} x={COLS[1]} title="DATA TYPES" unit={(n) => `${n.value} jobs · ${n.offers ?? 0} offers`} />
              <Column nodes={flow.consumers} x={COLS[2]} title="CONSUMERS" unit={(n) => `${n.value} jobs`} />
            </svg>
          </div>
        )}
      </QueryState>
      <p className="small muted">A type served by several producers counts in each producer's band: DME does not record which producer serves a job.</p>
      {cut && <p className="gap-note">Drawn from the first {FLOW_LIMIT} of {formatCount(total ?? null)} data jobs; the table below pages through all of them.</p>}
      {offersCut && <p className="gap-note">Offer counts are from the first {FLOW_LIMIT} offers.</p>}
      <p className="gap-note">Late deliveries are not shown: DME does not record when a job last delivered.</p>
    </Card>
  );
}
