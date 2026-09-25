import { pipelineSteps } from "../lib/domain";

/** A single metric over time. `floor` draws the guard-KPI floor line
 * (AI/ML MLMF guardKpiFloor) so breaches read at a glance. */
export function Sparkline({ points, width = 220, height = 48, floor, label }: {
  points: { t: string; v: number }[]; width?: number; height?: number; floor?: number; label?: string;
}) {
  if (points.length === 0) return <div className="spark-empty muted">no data</div>;
  const values = points.map((p) => p.v).concat(floor !== undefined ? [floor] : []);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 4;
  const x = (i: number) => (points.length === 1 ? width / 2 : pad + (i * (width - 2 * pad)) / (points.length - 1));
  const y = (v: number) => height - pad - ((v - min) * (height - 2 * pad)) / span;
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const breached = floor !== undefined && last.v < floor;
  return (
    <figure className="spark">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label ?? "sparkline"}>
        {floor !== undefined && <line x1={0} x2={width} y1={y(floor)} y2={y(floor)} className="spark-floor" />}
        <path d={path} className="spark-line" fill="none" />
        {points.map((p, i) => (
          <circle key={i} cx={x(i)} cy={y(p.v)} r={i === points.length - 1 ? 3 : 1.5}
            className={floor !== undefined && p.v < floor ? "spark-dot bad" : "spark-dot"}>
            <title>{`${p.v} @ ${new Date(p.t).toLocaleString()}`}</title>
          </circle>
        ))}
      </svg>
      <figcaption>
        {label && <span className="muted">{label} </span>}
        <strong className={breached ? "text-bad" : ""}>{formatNum(last.v)}</strong>
        {floor !== undefined && <span className="muted"> / floor {formatNum(floor)}</span>}
      </figcaption>
    </figure>
  );
}

function formatNum(v: number): string {
  return Math.abs(v) >= 100 ? v.toFixed(0) : Number(v.toPrecision(3)).toString();
}

/** AI/ML model lifecycle (REGISTERED -> ... -> ACTIVE), current step highlighted. */
export function FsmStepper({ state }: { state: string }) {
  return (
    <ol className="stepper" aria-label="model lifecycle">
      {pipelineSteps(state).map((s) => (
        <li key={s.state} className={`step ${s.status}`}><span className="step-dot" />{s.state}</li>
      ))}
      {state === "DEPRECATED" && <li className="step current deprecated"><span className="step-dot" />DEPRECATED</li>}
    </ol>
  );
}

/** Horizontal stacked bar of counts (alarm severities, instance states). */
export function CountBar({ parts }: { parts: { key: string; value: number; className: string }[] }) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  if (total === 0) return <div className="countbar empty" />;
  return (
    <div className="countbar" role="img" aria-label={parts.map((p) => `${p.key} ${p.value}`).join(", ")}>
      {parts.filter((p) => p.value > 0).map((p) => (
        <span key={p.key} className={p.className} style={{ flexGrow: p.value }} title={`${p.key}: ${p.value}`} />
      ))}
    </div>
  );
}
