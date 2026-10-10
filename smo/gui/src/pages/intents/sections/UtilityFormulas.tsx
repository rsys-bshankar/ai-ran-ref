/** Section `intents.formulas` (Utility formulas tab, feature 10): the TS 28.312 IntentUtilityFormula resources of Intent Service, server-paged:
 * the utility function, its weighted parameters, scale and offset. Read-only: the BFF exposes no write on formulas (README, Known limits). An
 * intent names its formula in `attributes.intentUtilityFormulaRef`; how many intents use each is not served. */
import { Card, Id } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { ServerTable } from "../../../kit/ServerTable";
import { FORMULAS } from "../data/queries";

/** One formula as Intent Service answers it. */
export interface UtilityFormula { id: string; attributes: { utilityFunctionId: string; utilityParameterList: { parameterName?: string | null; parameterWeight?: number | null }[]; utilityScale: number; utilityOffset: number } }

/** The tab. */
export function UtilityFormulas() {
  return (
    <div className="stack">
      <Callout tone="volt">A utility formula turns an intent's expectation results into one score: score = scale × f(weighted parameters) + offset. Handlers report utility results against it.</Callout>
      <Card section="intents.formulas" title="Intent utility formulas" sub="Intent Service · TS 28.312 IntentUtilityFormula">
        <ServerTable<UtilityFormula> path={FORMULAS} rowKey={(f) => f.id} empty="No utility formula defined." columns={[
          { header: "Formula", render: (f) => <Id value={f.id} /> },
          { header: "Function", render: (f) => <code className="small">{f.attributes.utilityFunctionId}</code> },
          { header: "Parameters", render: (f) => f.attributes.utilityParameterList.map((p) => `${p.parameterName ?? "?"}${p.parameterWeight != null ? ` × ${p.parameterWeight}` : ""}`).join(", ") || <span className="muted">—</span> },
          { header: "Scale", render: (f) => <span className="mono">{f.attributes.utilityScale}</span> },
          { header: "Offset", render: (f) => <span className="mono">{f.attributes.utilityOffset}</span> },
        ]} />
      </Card>
    </div>
  );
}
