/** The left half of the sign-in page (`login.brand`, handoff `Login.dc.html`): product name, headline, the sector-ring graphic and the
 * "A product of Radisys" wordmark on a white chip. Static: no data, no inputs (the form tests count the inputs of the page). */

/** The brand panel. Hidden graphic is decorative (`aria-hidden`); the wordmark keeps its alt text. */
export function BrandPanel() {
  return (
    <section className="login-brand" data-section="login.brand" aria-label="AI-RAN SMO">
      <div className="brand big">
        <img src="/brand/radisys-mark.png" alt="" width={48} height={48} />
        <div><strong>AI-RAN SMO</strong><span>Operator Console · Service Management &amp; Orchestration</span></div>
      </div>
      <SectorRings />
      <div className="col" style={{ gap: 18, position: "relative" }}>
        <div className="eyebrow">O-RAN Non-RT RIC · R1 · O1 · O2</div>
        <h1>Run the RAN with rApps you can trust.</h1>
        <p>Every change an rApp proposes is limited, explained and, when you choose, held for a person to approve before it touches the network.</p>
        <div className="row wrap" style={{ gap: 8 }}>
          <span className="chip">Closed-loop automation</span><span className="chip">AI/ML lifecycle</span><span className="chip">Intent-driven</span>
        </div>
        <span className="wordmark-chip">A product of <img src="/brand/radisys-wordmark.png" alt="Radisys" height={22} /></span>
      </div>
    </section>
  );
}

/** The sector-ring graphic: four range rings and three cell sectors around a site, the accent sector filled. Colours come from styles.css. */
function SectorRings() {
  return (
    <svg className="login-rings" width={560} height={420} viewBox="0 0 560 420" aria-hidden="true">
      {[60, 120, 180, 240].map((r) => <circle key={r} cx={300} cy={210} r={r} strokeWidth={1.5} />)}
      <path d="M300 210 L330 30 A182 182 0 0 1 470 270 Z" strokeWidth={1.5} />
      <path d="M300 210 L450 320 A182 182 0 0 1 150 320 Z" strokeWidth={1.5} opacity={0.45} />
      <path d="M300 210 L130 280 A182 182 0 0 1 270 30 Z" strokeWidth={1.5} opacity={0.3} />
    </svg>
  );
}
