/** Element detail · managed-object tree (`element.mo`): the element's containment tree from its root DN (`GET /managed-objects/{dn}`).
 * A node's children load only when it is opened (`/children?limit=50`, "more" asks for 50 more), and "Expand 3 levels" loads the selected
 * node's subtree in one call (`/subtree?depth=3`, at most 1000 nodes). Selecting a node shows it in the attributes box (`?mo=`). "Refresh
 * from element" (`POST …/managed-objects/refresh`, a walk of the element's server) is an operator's call; a viewer does not see the button. */
import { useEffect, useState } from "react";

import { ActionButton, Card } from "../../../components/ui";
import { Empty, ErrorRetry, Skeleton } from "../../../kit/states";
import { refreshPath, useChildren, useMo, useSelectedMo, useSubtree } from "../data/queries";
import { rootDn, type ManagedObject } from "../data/types";

const STEP = 50;

/** Shared tree state: open nodes, children a subtree answer already holds, the selection. */
interface TreeState { open: Set<string>; toggle: (dn: string) => void; preloaded: Map<string, ManagedObject[]>; selected: string | null; select: (dn: string) => void }

/** The tree card of element `me`. */
export function MoTree({ me }: { me: string }) {
  const root = useMo(rootDn(me));
  const [selected, select] = useSelectedMo();
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [preloaded, setPreloaded] = useState<Map<string, ManagedObject[]>>(() => new Map());
  const [expandAt, setExpandAt] = useState<string | null>(null);
  const subtree = useSubtree(expandAt, 3);
  useEffect(() => {
    if (!subtree.data) return;
    const kids = new Map(preloaded), opened = new Set(open);
    const walk = (n: ManagedObject) => { if (n.children) { kids.set(n.dn, n.children); opened.add(n.dn); n.children.forEach(walk); } };
    walk(subtree.data.tree);
    setPreloaded(kids);
    setOpen(opened);
  }, [subtree.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const state: TreeState = {
    open, preloaded, selected, select,
    toggle: (dn) => setOpen((s) => { const n = new Set(s); if (n.has(dn)) n.delete(dn); else n.add(dn); return n; }),
  };
  return (
    <Card section="element.mo" title="Managed objects" sub="containment tree · children load on expand"
      actions={<>
        <button type="button" className="btn small" disabled={!selected || subtree.isFetching} onClick={() => { setExpandAt(selected); void (expandAt === selected && subtree.refetch()); }}>Expand 3 levels</button>
        <ActionButton label="Refresh from element" action={{ method: "POST", path: refreshPath(me), success: "Tree refreshed from the element" }} />
      </>}>
      {subtree.data?.truncated && <p className="gap-note">The subtree stopped at 1000 nodes; open deeper nodes one by one.</p>}
      {root.error && (root.error.status === 404 ? <Empty title="This element is not in the containment tree yet." /> : <ErrorRetry error={root.error} onRetry={() => void root.refetch()} />)}
      {!root.data && !root.error && <Skeleton lines={5} />}
      {root.data && <ul className="mo-tree" role="tree" aria-label="Managed objects"><Node mo={root.data} depth={0} t={state} /></ul>}
    </Card>
  );
}

/** One node and, when open, its children. */
function Node({ mo, depth, t }: { mo: ManagedObject; depth: number; t: TreeState }) {
  const isOpen = t.open.has(mo.dn);
  const pre = t.preloaded.get(mo.dn);
  const [limit, setLimit] = useState(STEP);
  const kids = useChildren(mo.dn, limit, isOpen && (!pre || limit > STEP));
  const children = kids.data?.items ?? pre;
  const total = kids.data?.total ?? pre?.length;
  const label = `${mo.class}=${mo.id}`;
  return (
    <li role="treeitem" aria-expanded={isOpen} aria-selected={t.selected === mo.dn}>
      <div className={`mo-row${t.selected === mo.dn ? " on" : ""}`} style={{ paddingLeft: 8 + depth * 16 }}>
        <button type="button" className="mo-toggle" aria-label={`${isOpen ? "Close" : "Open"} ${label}`} onClick={() => t.toggle(mo.dn)}>{isOpen ? "▾" : "▸"}</button>
        <button type="button" className="mo-label mono small" onClick={() => t.select(mo.dn)}>{label}</button>
        {mo.source === "walk" && <span className="xs muted">walked</span>}
      </div>
      {isOpen && (
        <ul role="group">
          {kids.isLoading && !pre && <li className="muted small" style={{ paddingLeft: 24 + depth * 16 }}>Loading…</li>}
          {kids.error && <li style={{ paddingLeft: 24 + depth * 16 }}><ErrorRetry error={kids.error} onRetry={() => void kids.refetch()} /></li>}
          {children?.length === 0 && <li className="muted small" style={{ paddingLeft: 24 + depth * 16 }}>No children.</li>}
          {children?.map((c) => <Node key={c.dn} mo={c} depth={depth + 1} t={t} />)}
          {children && total !== undefined && total > children.length && (
            <li style={{ paddingLeft: 24 + depth * 16 }}><button type="button" className="btn small ghost" onClick={() => setLimit(Math.max(limit, children.length) + STEP)}>{total - children.length} more…</button></li>
          )}
        </ul>
      )}
    </li>
  );
}
