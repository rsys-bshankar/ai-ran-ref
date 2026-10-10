/** Preferences › Preview (section `preferences.preview`): a few of the console's primitives drawn with the previewed preferences (they are
 * already on `<html>`), so the user sees tiles, chips, badges and buttons before saving. Sample content only, labelled as such. */
import { Card } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Kpi } from "../../../kit/Kpi";
import { Meter } from "../../../kit/Meter";

/** The preview card. */
export function LivePreview() {
  return (
    <Card title="Preview" sub="sample content" section="preferences.preview" className="preview-sticky">
      <div className="preview-box">
        <Kpi label="Sample tile" value="98.0" unit="%" foot="sample figure, not live data">
          <Meter parts={[{ key: "ok", value: 92, tone: "ok" }, { key: "warn", value: 6, tone: "warn" }, { key: "bad", value: 2, tone: "bad" }]} />
        </Kpi>
        <div className="row wrap"><span className="sev sev-cr">Critical</span><span className="sev sev-mj">Major</span><Badge tone="ok">RUNNING</Badge><Badge tone="warn">PENDING</Badge><Badge tone="volt">AUTONOMOUS</Badge></div>
        <div className="row wrap"><button type="button" className="btn primary">Approve</button><button type="button" className="btn">Open</button><button type="button" className="btn danger">Stop</button></div>
        <a href="#preview">A link looks like this →</a>
      </div>
      <p className="xs muted">Stored with your user in the GUI backend. This browser keeps a copy so pages open in your theme straight away.</p>
    </Card>
  );
}
