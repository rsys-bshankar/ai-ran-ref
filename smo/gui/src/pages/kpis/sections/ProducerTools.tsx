/** Section `kpis.producerTools` (RAN Analytics tab, admin-only, gated by the caller): act as an MDAF analytics producer (call flow 08):
 * register a producer (`POST /ran-analytics/producers`, which also registers `mdaf.<type>` with SME) and publish a report (`POST /mdaf/reports`),
 * pushed best-effort to subscribers with a notification destination. */
import { useState } from "react";

import { ActionButton, Card, Field } from "../../../components/ui";
import { parseJsonObject } from "../../../lib/domain";
import { MDAF_REPORTS, PRODUCERS, useDmeTypes } from "../data/queries";
import { useAnalyticsTypes } from "./AnalyticsLegacy";

/** The two admin forms side by side. */
export function ProducerTools() {
  const types = useAnalyticsTypes();
  const dmeTypes = useDmeTypes();
  const [producer, setProducer] = useState("rapp-mdaf-1");
  const [type, setType] = useState("coverage-issue-analysis");
  const [inputs, setInputs] = useState<string[]>([]);
  const [reportType, setReportType] = useState("");
  const [output, setOutput] = useState('{"coverageHoles": 2, "worstCell": "cell-7"}');
  const parsed = parseJsonObject(output);
  return (
    <Card section="kpis.producerTools" title="Producer side (call flow 08)" actions={<span className="muted small">Admin: act as an MDAF analytics producer</span>}>
      <div className="grid cols-2">
        <div>
          <h3>Register a producer</h3>
          <p className="muted small">Also registers <code>mdaf.&lt;type&gt;</code> with SME so consumers can discover it — the producer must be a registered SME provider (Data &amp; Exposure → SME).</p>
          <div className="form">
            <Field label="Producer ID"><input value={producer} onChange={(e) => setProducer(e.target.value)} /></Field>
            <Field label="Analytics type"><input value={type} onChange={(e) => setType(e.target.value)} /></Field>
            <Field label="DME input types" hint="Ctrl/Cmd-click for several">
              <select multiple size={Math.min(4, Math.max(2, dmeTypes.data?.length ?? 2))} value={inputs} onChange={(e) => setInputs([...e.target.selectedOptions].map((o) => o.value))}>
                {dmeTypes.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}
              </select>
            </Field>
          </div>
          <ActionButton label="Register producer" tone="primary" disabled={!producer || !type} action={{
            method: "POST", path: PRODUCERS, query: { producer_id: producer, analytics_type: type },
            json: { dme_input_types: inputs, output_schema: { type: "object" } }, success: "Producer registered",
          }} />
        </div>
        <div>
          <h3>Publish a report</h3>
          <p className="muted small">Pushed best-effort to subscribers with a notification destination; poll-only subscribers read it back via the reports list.</p>
          <div className="form">
            <Field label="Analytics type"><input list="an-types-pub" value={reportType} onChange={(e) => setReportType(e.target.value)} /><datalist id="an-types-pub">{types.map((t) => <option key={t} value={t} />)}</datalist></Field>
            <Field label="Output (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><textarea rows={3} value={output} onChange={(e) => setOutput(e.target.value)} spellCheck={false} /></Field>
          </div>
          <ActionButton label="Publish report" tone="primary" disabled={!reportType || !parsed.ok} action={{
            method: "POST", path: MDAF_REPORTS, query: { analytics_type: reportType },
            json: { output: parsed.ok ? parsed.value : {}, input_sources: inputs }, success: "Report published",
          }} />
        </div>
      </div>
    </Card>
  );
}
