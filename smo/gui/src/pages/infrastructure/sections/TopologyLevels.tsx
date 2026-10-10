/** Infrastructure → Topology, the graph box (`infrastructure.topology`): the TEIV topology collapsed by level (SCALE.md: Infrastructure). It shows
 * one level at a time, O-Clouds → their deployment managers, pools and workloads → a pool's resources → a resource's children, with a breadcrumb
 * back up, a "jump to node" search, and at most `CAP` tiles on screen ("+N more"). Each tile is coloured by health and says it in words. Clicking a
 * tile selects it for the inspector and, when it has children, opens its level. The tree comes from `data/topology.ts`. */
import { useMemo, useState } from "react";

import { Card } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Empty, QueryState } from "../../../kit/states";
import { TOPOLOGY_LIMIT, useTopology, useTopologyAlarms, useTopologyWorkloads } from "../data/queries";
import { buildTopology, HEALTH_LABEL, HEALTH_TONE, KIND_LABEL, pathTo, searchNodes } from "../data/topology";
import type { NodeKind, TopoNode, TopoTree } from "../data/types";

/** The most tiles one level draws (SCALE.md: about 200 visible nodes). */
export const CAP = 200;

const GROUPS: { kind: NodeKind; label: string }[] = [
  { kind: "dm", label: "Deployment managers" }, { kind: "pool", label: "Resource pools" }, { kind: "resource", label: "Resources" },
  { kind: "workload", label: "Workloads (NF deployments)" }, { kind: "ocloud", label: "O-Clouds" },
];

/** The tree the box and its inspector share; `null` until the topology export has answered. */
export function useTopologyTree(): { tree: TopoTree | null; q: ReturnType<typeof useTopology> } {
  const q = useTopology();
  const workloads = useTopologyWorkloads();
  const alarms = useTopologyAlarms();
  const tree = useMemo(() => {
    if (!q.data) return null;
    const cut = (p: { items: unknown[]; total?: number; hasMore?: boolean } | undefined) => p && (p.total !== undefined ? p.total > p.items.length : !!p.hasMore) ? { shown: p.items.length, total: p.total ?? null } : null;
    return buildTopology({ topology: q.data, workloads: workloads.data?.items, workloadsCut: cut(workloads.data), alarms: alarms.data?.items, alarmsCut: cut(alarms.data) });
  }, [q.data, workloads.data, alarms.data]);
  return { tree, q };
}

/** The graph box. `path` is the open level (root first); `selected` the node the inspector shows. */
export function TopologyLevels({ tree, q, path, onPath, selected, onSelect }: {
  tree: TopoTree | null; q: ReturnType<typeof useTopology>; path: string[]; onPath: (p: string[]) => void; selected: string | null; onSelect: (id: string) => void;
}) {
  const [search, setSearch] = useState("");
  const open = path.length ? tree?.nodes.get(path[path.length - 1]) : undefined;
  const ids = (open ? open.children : tree?.roots) ?? [];
  const shown = ids.slice(0, CAP);
  const matches = tree ? searchNodes(tree, search) : [];
  const jump = (n: TopoNode) => {
    const p = pathTo(tree!, n.id);
    onPath(n.children.length ? p : p.slice(0, -1));
    onSelect(n.id);
    setSearch("");
  };
  const pick = (n: TopoNode) => {
    onSelect(n.id);
    if (n.children.length) onPath([...path, n.id]);
  };
  return (
    <Card section="infrastructure.topology" title="TEIV topology" sub={`collapsed by level · one level at a time · at most ${CAP} nodes on screen`}
      actions={<label className="search" style={{ width: 240 }}><input aria-label="Jump to node" placeholder="Jump to node, pool, NF…" value={search} onChange={(e) => setSearch(e.target.value)} /></label>}>
      {matches.length > 0 && (
        <ul className="list" aria-label="Matching nodes">
          {matches.map((n) => <li key={n.id}><button type="button" className="btn small ghost" onClick={() => jump(n)}>{n.name}</button><span className="xs muted">{KIND_LABEL[n.kind]}</span></li>)}
        </ul>
      )}
      <QueryState q={q} isEmpty={() => !tree || tree.roots.length === 0} empty={<Empty title="FOCOM exports an empty topology.">Register a resource pool or provision a resource on the O-Cloud inventory tab.</Empty>}>
        {tree && <>
          <nav className="crumb" aria-label="Topology level">
            <button type="button" className="btn small ghost" onClick={() => onPath([])} aria-current={path.length === 0 ? "page" : undefined}>All O-Clouds</button>
            {path.map((id, i) => (
              <span key={id} className="row">
                <span aria-hidden>›</span>
                {i === path.length - 1 ? <strong>{tree.nodes.get(id)?.name}</strong>
                  : <button type="button" className="btn small ghost" onClick={() => onPath(path.slice(0, i + 1))}>{tree.nodes.get(id)?.name}</button>}
              </span>
            ))}
          </nav>
          {GROUPS.map(({ kind, label }) => {
            const group = shown.map((id) => tree.nodes.get(id)!).filter((n) => n.kind === kind);
            if (!group.length) return null;
            return (
              <div key={kind} className="stack" style={{ gap: 8 }}>
                <div className="eyebrow">{open ? `${open.name} · ` : ""}{label} · {group.length}</div>
                <div className="topo-tiles">
                  {group.map((n) => (
                    <button key={n.id} type="button" className={`tile topo-tile h-${n.health}${selected === n.id ? " on" : ""}`} onClick={() => pick(n)}
                      aria-pressed={selected === n.id} title={`${KIND_LABEL[n.kind]} ${n.ref} · ${HEALTH_LABEL[n.health]}`}>
                      <span className="mono xs topo-name">{n.name}</span>
                      <span className="row between xs">
                        <Badge tone={HEALTH_TONE[n.health]} plain>{n.kind === "workload" ? n.state : HEALTH_LABEL[n.health]}</Badge>
                        {n.children.length > 0 && <span className="muted">{n.children.length} ›</span>}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
          {ids.length > CAP && <span className="xs muted">+{(ids.length - CAP).toLocaleString("en-US")} more · sorted by worst health · use “Jump to node” to reach the rest</span>}
          {ids.length === 0 && <Empty title="Nothing under this node." />}
          <div className="legend">
            <span><i className="h-ok" />Healthy</span><span><i className="h-warn" />Degraded</span><span><i className="h-bad" />Faulty</span><span><i className="h-unknown" />No health data</span>
          </div>
          {tree.notes.map((n) => <p key={n} className="gap-note">{n}</p>)}
          <p className="gap-note">Colour by GPU or CPU is not offered: no module serves node utilisation yet. At most {TOPOLOGY_LIMIT} workloads and alarms are read.</p>
        </>}
      </QueryState>
    </Card>
  );
}
