/** Infrastructure → Topology, the tab's two boxes side by side: the level graph (`TopologyLevels`) and the inspector (`NodeInspector`), which
 * share one tree and one selection. Holds only that shared state (the open level path and the selected node); each box has its own boundary. */
import { useState } from "react";

import { SectionBoundary } from "../../../kit/SectionBoundary";
import { NodeInspector } from "./NodeInspector";
import { TopologyLevels, useTopologyTree } from "./TopologyLevels";

/** The tab. */
export function TopologyTab() {
  const { tree, q } = useTopologyTree();
  const [path, setPath] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="grid g-main-side" style={{ alignItems: "start" }}>
      <SectionBoundary id="infrastructure.topology">
        <TopologyLevels tree={tree} q={q} path={path} onPath={setPath} selected={selected} onSelect={setSelected} />
      </SectionBoundary>
      <SectionBoundary id="infrastructure.inspector"><NodeInspector tree={tree} selected={selected} /></SectionBoundary>
    </div>
  );
}
