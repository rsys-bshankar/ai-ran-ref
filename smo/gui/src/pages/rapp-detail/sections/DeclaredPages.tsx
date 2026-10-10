/** The rApp's own operator page, as its package declares it (PR-GUI-8, ADR 0004), drawn by `components/OperatorUi` DeclaredPage unchanged,
 * below the platform overview. A rApp with no declaration, or one this console cannot read, says so. Section id `rapp.declared`. */
import type { RappPage } from "../../../api/rapps";
import { DeclaredPage } from "../../../components/OperatorUi";
import { Card } from "../../../components/ui";

/** The declared page of `data`. */
export function DeclaredPages({ data }: { data: RappPage }) {
  return (
    <div className="stack" data-section="rapp.declared">
      <h2 className="section-title">Operator page</h2>
      {data.declarationState === "declared" && data.declaration && (
        <DeclaredPage instanceId={data.instanceId} declaration={data.declaration} canChange={data.canChange} operatorApiRegistered={data.operatorApiRegistered} />
      )}
      {data.declarationState === "unreadable" && (
        <Card><p className="muted">The page declared in this rApp's package could not be read by this console, so only the overview is shown.</p></Card>
      )}
      {data.declarationState === "none" && (
        <Card><p className="muted">This rApp's package declares no operator page. The platform overview above is all there is for it.</p></Card>
      )}
    </div>
  );
}
