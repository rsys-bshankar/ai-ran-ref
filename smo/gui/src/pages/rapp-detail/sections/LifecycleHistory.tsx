/** The rApp detail "Lifecycle history" box: the instance's committed upgrades and rollbacks (rApp Management `/versions`), ten at a time,
 * with Roll back — the same `VersionHistory` the rApps drawer shows. Section id `rapp.history`. */
import { Card } from "../../../components/ui";
import { Skeleton } from "../../../kit/states";
import { VersionHistory } from "../../rapps/sections/VersionHistory";
import { useInstance } from "../data/queries";

/** The box of instance `id`. */
export function LifecycleHistory({ id }: { id: string }) {
  const inst = useInstance(id);
  return (
    <Card section="rapp.history">
      {inst.data ? <VersionHistory id={id} state={inst.data.state} title="Lifecycle history" /> : <><h3>Lifecycle history</h3><Skeleton lines={2} /></>}
    </Card>
  );
}
