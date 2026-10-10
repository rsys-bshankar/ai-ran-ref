/** The "Deploy" dialog of an AVAILABLE package (CreateInstance, call flow 01): configuration, autonomy mode (fixed for the instance's life),
 * region scope for AUTONOMOUS and the optional operator API base. Opened from the packages table. */
import { useState, type FormEvent } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { Package } from "../../../api/types";
import { Field, Modal } from "../../../components/ui";
import { parseJsonObject } from "../../../lib/domain";
import { INSTANCES_PATH } from "../data/queries";

/** The dialog; closes itself once rApp Management accepted the instance. */
export function CreateInstance({ pkg, onClose }: { pkg: Package; onClose: () => void }) {
  const [config, setConfig] = useState("{}");
  const [autonomyMode, setAutonomyMode] = useState("SHADOW");
  const [regionScope, setRegionScope] = useState("{}");
  const [operatorApiBase, setOperatorApiBase] = useState("");
  const action = useSmoAction();
  const parsed = parseJsonObject(config);
  const parsedScope = parseJsonObject(regionScope);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!parsed.ok || !parsedScope.ok) return;
    action.mutate({ method: "POST", path: INSTANCES_PATH, json: {
      packageId: pkg.packageId, config: parsed.value, autonomyMode,
      regionScope: autonomyMode === "AUTONOMOUS" ? parsedScope.value : null,
      ...(operatorApiBase.trim() ? { operatorApiBase: operatorApiBase.trim() } : {}),
    }, success: "Instance created (DEPLOYING)" }, { onSuccess: onClose });
  };
  return (
    <Modal title={`Deploy ${pkg.name} ${pkg.version}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <p className="muted small">rApp Management checks the package is AVAILABLE, instantiates it on NFO from its NF deployment descriptor, and registers package usage.</p>
        <Field label="Instance configuration (JSON)" hint={parsed.ok ? "Optional. requiredResourceTypeId is passed to NFO for placement." : <span className="text-bad">{parsed.error}</span>}>
          <textarea rows={6} value={config} onChange={(e) => setConfig(e.target.value)} spellCheck={false} />
        </Field>
        <Field label="Autonomy mode" hint="Fixed for this instance's lifetime — how it may act on its own AI/ML inference outcomes (HISTORY.md OI-6.3).">
          <select value={autonomyMode} onChange={(e) => setAutonomyMode(e.target.value)}>
            <option value="SHADOW">SHADOW — observe-only, never dispatched</option>
            <option value="ASSIST">ASSIST — operator scopes before dispatch</option>
            <option value="AUTONOMOUS">AUTONOMOUS — dispatched at a pre-configured scope</option>
          </select>
        </Field>
        {autonomyMode === "AUTONOMOUS" && (
          <Field label="Region scope (JSON)" hint={parsedScope.ok ? "Which RAN nodes/cells/slices AUTONOMOUS dispatches are addressed to." : <span className="text-bad">{parsedScope.error}</span>}>
            <textarea rows={3} value={regionScope} onChange={(e) => setRegionScope(e.target.value)} spellCheck={false} />
          </Field>
        )}
        <Field label="Operator API base URL (optional)" hint="Where the rApp serves the routes its operator page declares, for example http://my-rapp:8000. A rApp that runs with this instance's own credentials can register it itself; the page of a rApp without one shows only the overview.">
          <input value={operatorApiBase} onChange={(e) => setOperatorApiBase(e.target.value)} placeholder="http://my-rapp:8000" pattern="https?://.+" />
        </Field>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary" disabled={!parsed.ok || !parsedScope.ok || action.isPending}>Deploy</button></div>
      </form>
    </Modal>
  );
}
