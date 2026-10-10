/** The rApp detail "Lifecycle" box: the instance as rApp Management holds it (state, package, NFO deployment, autonomy, region and access
 * scope) with the lifecycle actions, Upgrade included. Section id `rapp.instance`. */
import { useState } from "react";

import { Card, Json, KeyValue, StateBadge } from "../../../components/ui";
import { QueryState } from "../../../kit/states";
import { describeScope } from "../../../lib/domain";
import { InstanceActions } from "../../rapps/sections/InstanceActions";
import { UpgradeModal } from "../../rapps/sections/UpgradeModal";
import { useInstance } from "../data/queries";

/** The box of instance `id`. */
export function InstanceState({ id }: { id: string }) {
  const inst = useInstance(id);
  const [upgrading, setUpgrading] = useState(false);
  const d = inst.data;
  return (
    <Card section="rapp.instance" title="Lifecycle" actions={d ? <InstanceActions inst={d} withUpgrade={() => setUpgrading(true)} /> : undefined}>
      <QueryState q={inst} isEmpty={() => false}>
        {d && <KeyValue items={[
          ["State", <StateBadge key="s" state={d.state} />], ["Package", <code key="p">{d.packageId}</code>],
          ["NFO deployment", d.workloadRef && <code key="w">{d.workloadRef}</code>],
          ["Pending upgrade to", d.pendingUpgradeInstanceId && <code key="u">{d.pendingUpgradeInstanceId}</code>],
          ["Autonomy mode", <StateBadge key="a" state={d.autonomyMode} />],
          ["Region scope", d.regionScope ? <Json value={d.regionScope} /> : <span className="muted">—</span>],
          ["Access scope", <span key="z" title="Which managed elements, by region and tenant, this rApp may touch (set when the instance was created)">{describeScope(d.authzScope)}</span>],
        ]} />}
      </QueryState>
      {upgrading && d && <UpgradeModal inst={d} onClose={() => setUpgrading(false)} />}
    </Card>
  );
}
