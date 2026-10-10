/** The package drawer: identity, ASD descriptor, SME registration, artifacts, priming and the usage registrations that guard delete and
 * deprime (call flow 06). Opened by a row click in `PackagesTable`. */
import { Link } from "react-router-dom";

import type { Package } from "../../../api/types";
import { ActionButton, Can, DataTable, Drawer, Id, KeyValue, StateBadge } from "../../../components/ui";
import { formatTime } from "../../../lib/domain";
import { packageBase, usePackageArtifacts, usePackageUsage } from "../data/queries";

/** What Onboarding reports about a package's signature (GUI-10.2). Onboarding serves only `signatureVerified`: true when the CSAR's signature
 * verified against the publisher keys of its trust store (`ONBOARDING_TRUST_STORE`), false when it was accepted without that (unsigned, with no
 * signature required); it serves no trust-store mode and no signer, so neither is claimed here. */
export function signatureText(verified: boolean | null | undefined): string {
  if (verified === true) return "verified against Onboarding's trust store";
  if (verified === false) return "not verified (accepted unsigned: no signature was required)";
  return "not reported";
}

/** The drawer of `pkg`. */
export function PackageDrawer({ pkg, onClose }: { pkg: Package; onClose: () => void }) {
  const base = packageBase(pkg.packageId);
  const artifacts = usePackageArtifacts(pkg.packageId);
  const usage = usePackageUsage(pkg.packageId);
  const active = (usage.data ?? []).filter((u) => u.active);
  return (
    <Drawer title={`${pkg.name} ${pkg.version}`} onClose={onClose}>
      <div className="row between"><StateBadge state={pkg.state} /><Link className="btn small" to={`/flows/06?subject=${pkg.packageId}`}>Track in flow 06 →</Link></div>
      <KeyValue items={[
        ["Package ID", <code>{pkg.packageId}</code>], ["Vendor / type", `${pkg.vendor ?? "—"} / ${pkg.applicationType}`],
        ["TOSCA entry definitions", pkg.toscaEntryDefinitions], ["Signature", signatureText(pkg.signatureVerified)],
        ["NF deployment descriptor", pkg.nfDeploymentDescriptorId && <code>{pkg.nfDeploymentDescriptorId}</code>],
        ["Instances using it", usage.data ? String(active.length) : "…"],
        ["AI capabilities", pkg.aiCapabilities ? <code className="small">{JSON.stringify(pkg.aiCapabilities)}</code> : <span className="muted">none declared</span>],
      ]} />
      <h3>ASD descriptor</h3>
      <KeyValue items={[
        ["Descriptor ID", pkg.descriptorId ? <code className="small">{pkg.descriptorId}</code> : <span className="muted">not declared</span>],
        ["Invariant ID", pkg.descriptorInvariantId ? <code className="small">{pkg.descriptorInvariantId}</code> : <span className="muted">not declared</span>],
        ["Descriptor version", pkg.descriptorVersion ?? <span className="muted">not declared</span>],
        ["ASD schema version", pkg.schemaVersion ?? <span className="muted">not declared</span>],
      ]} />
      <h3>SME registration</h3>
      <p className="muted small">
        {pkg.smeDeclarations
          ? `This package's CSAR declares ${pkg.smeDeclarations.providers.length} provider(s) and ${pkg.smeDeclarations.serviceApis.length} service API(s) — each deployed instance registers them with SME at bootstrap.`
          : "This package's CSAR declares no Files/Sme/ providers or service APIs — nothing is auto-registered with SME."}
      </p>
      <h3>Artifacts</h3>
      <DataTable rows={artifacts.data} error={artifacts.error} rowKey={(a) => a.artifactId} empty="No artifacts registered." columns={[
        { header: "Path", render: (a) => <code className="small">{a.path}</code> }, { header: "Access URL", render: (a) => <code className="small clip">{a.accessUrl}</code> },
      ]} />
      <h3>Priming</h3>
      <p className="muted small">
        {pkg.state === "PRIMED"
          ? active.length
            ? `PRIMED. Deprime is refused while ${active.length} usage registration(s) are active — terminate the instances using this package (or stop their usage) first.`
            : "PRIMED. No active usage, so deprime will succeed (PRIMED → DEPRIMING → AVAILABLE)."
          : pkg.state === "AVAILABLE"
            ? "AVAILABLE (commissioned). Prime pre-provisions the package: AVAILABLE → PRIMING → PRIMED."
            : `Priming applies to AVAILABLE packages; this one is ${pkg.state}.`}
      </p>
      {(pkg.state === "AVAILABLE" || pkg.state === "PRIMED") && (
        <div className="row gap">
          {pkg.state === "AVAILABLE"
            ? <ActionButton label="Prime" action={{ method: "POST", path: `${base}/prime`, success: "Package primed" }} />
            : <ActionButton label="Deprime" disabled={active.length > 0} title={active.length ? "Blocked by active usage" : undefined} action={{ method: "POST", path: `${base}/deprime`, success: "Package deprimed" }} />}
        </div>
      )}
      <h3>Usage registrations (cascade-delete guard)</h3>
      <p className="muted small">{active.length ? `${active.length} active registration(s): deprime and delete are blocked until they stop.` : "No active usage — delete and deprime are not blocked by usage."}</p>
      <DataTable rows={usage.data} error={usage.error} rowKey={(u) => u.registrationId} empty="No usage registrations." columns={[
        { header: "Consumer", render: (u) => <Id value={u.consumerId} /> },
        { header: "State", render: (u) => u.active ? <StateBadge state="ACTIVE" /> : <>stopped {formatTime(u.stoppedAt)}</> },
        { header: "", className: "actions", render: (u) => u.active && <ActionButton label="Stop" title="Simulates the consumer releasing the package"
          action={{ method: "POST", path: `${base}/usage/${u.registrationId}/stop`, success: "Usage stopped" }} /> },
      ]} />
      <Can method="POST" path={`${base}/usage/start`}>
        <div className="row gap"><ActionButton label="Register test usage" title="Simulates an instance holding this package, to exercise the guard"
          action={{ method: "POST", path: `${base}/usage/start`, query: { consumer_id: "smo-gui-test" }, success: "Usage registered" }} /></div>
      </Can>
    </Drawer>
  );
}
