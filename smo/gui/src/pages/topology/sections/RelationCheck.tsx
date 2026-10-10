/** RAN topology · relation check (`topology.check`): how managed object A stands to B in the containment tree (`GET /topology/relation`):
 * SAME, ANCESTOR, DESCENDANT, SIBLING, SAME_ELEMENT or DIFFERENT_ELEMENT; 404 when either DN is not in the tree. The inputs start from the
 * focused element's root DN. */
import { useState } from "react";

import { Card, Field } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Callout } from "../../../kit/Callout";
import { rootDn } from "../../element/data/types";
import { useRelation, useTopologyParams } from "../data/queries";

/** What each relation means, in words. */
const MEANING: Record<string, string> = {
  SAME: "the same managed object",
  ANCESTOR: "A contains B",
  DESCENDANT: "B contains A",
  SIBLING: "same parent",
  SAME_ELEMENT: "different branches of one element (one box, one fault domain)",
  DIFFERENT_ELEMENT: "different managed elements (a fault may spread by radio, not through the box)",
};

/** The check card. */
export function RelationCheck() {
  const { me } = useTopologyParams();
  const seed = me ? `${rootDn(me)},` : "ManagedElement=";
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [asked, setAsked] = useState<{ a: string; b: string }>({ a: "", b: "" });
  const relation = useRelation(asked.a, asked.b);
  return (
    <Card section="topology.check" title="Relation check" sub="/topology/relation">
      <form className="form" onSubmit={(e) => { e.preventDefault(); setAsked({ a: a.trim(), b: b.trim() }); }}>
        <Field label="A (DN)"><input className="mono" value={a} placeholder={seed} onChange={(e) => setA(e.target.value)} /></Field>
        <Field label="B (DN)"><input className="mono" value={b} placeholder={seed} onChange={(e) => setB(e.target.value)} /></Field>
        <div className="row"><button type="submit" className="btn small" disabled={!a.trim() || !b.trim()}>Check</button></div>
      </form>
      {relation.isFetching && !relation.data && <p className="muted small">Checking…</p>}
      {relation.error && <Callout tone="warn" title={relation.error.status === 404 ? "Not in the containment tree" : "The check failed"}>{relation.error.message}</Callout>}
      {relation.data && (
        <Callout tone="volt" title={<span className="row wrap"><Badge tone="volt">{relation.data.relation}</Badge>{MEANING[relation.data.relation] ?? ""}</span>} />
      )}
    </Card>
  );
}
