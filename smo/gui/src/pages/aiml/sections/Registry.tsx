/** Sections `aiml.repositories`, `aiml.storages` and `aiml.artifactVersions` (Registry tab, feature 8): MLMR's TS 28.105 model repositories
 * (label, model and group counts), its model storages (profiles, addresses, supported features) and every model's artifact versions as download
 * links (`GET /mlmr/models/{id}/artifact/{version}` through the BFF). All three are server-paged and read-only: the BFF exposes no write on
 * repositories or storages (README, Known limits). MLMR serves no list of a model's artifact versions; they are counted from its
 * `artifactLocation` (`model-artifact:<model id>:<latest version>`, data/board.ts `ARTIFACT_LOCATION_RE`). */
import type { Model } from "../../../api/types";
import { Card, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { artifactVersions } from "../data/board";
import { MODELS, REPOSITORIES, STORAGES, artifactHref } from "../data/queries";
import type { ModelRepository, ModelStorage } from "../data/types";

/** Model repositories. */
export function Repositories() {
  return (
    <Card section="aiml.repositories" title="Model repositories" sub="MLMR · TS 28.105 MLModelRepository">
      <ServerTable<ModelRepository> path={REPOSITORIES} rowKey={(r) => r.id} empty="No model repository." columns={[
        { header: "Repository", render: (r) => <Id value={r.id} /> },
        { header: "Label", render: (r) => r.attributes.userLabel ?? <span className="muted">—</span> },
        { header: "Models", render: (r) => <span className="num">{r.MLModel.length}</span> },
        { header: "Coordination groups", render: (r) => <span className="num">{r.MLModelCoordinationGroup.length}</span> },
      ]} />
    </Card>
  );
}

/** Model storages. */
export function Storages() {
  return (
    <Card section="aiml.storages" title="Model storages" sub="MLMR · where artifacts are kept">
      <ServerTable<ModelStorage> path={STORAGES} rowKey={(s) => s.storageId} empty="No model storage." columns={[
        { header: "Storage", render: (s) => <Id value={s.storageId} /> },
        { header: "Model profiles", render: (s) => <span className="num">{s.mlModels?.length ?? 0}</span> },
        { header: "Addresses", render: (s) => s.mlModelsAddresses?.length ? <code className="small">{s.mlModelsAddresses.join(", ")}</code> : <span className="muted">—</span> },
        { header: "Supported features", render: (s) => s.suppFeat ?? <span className="muted">—</span> },
      ]} />
    </Card>
  );
}

/** Artifact versions of every model. */
export function ArtifactVersions() {
  return (
    <Card section="aiml.artifactVersions" title="Artifact versions" sub="Download any stored version">
      <ServerTable<Model> path={MODELS} rowKey={(m) => m.modelId} empty="No models registered." columns={[
        { header: "Model", render: (m) => <><strong>{m.modelType}</strong> <span className="muted">v{m.version}</span></> },
        { header: "Location", render: (m) => m.artifactLocation ? <code className="small">{m.artifactLocation}</code> : <span className="muted">—</span> },
        { header: "Versions", render: (m) => {
          const versions = artifactVersions(m.artifactLocation, m.modelId);
          return versions.length === 0 ? <span className="muted">none</span>
            : <span className="row wrap">{versions.slice(0, 10).map((v) => <a key={v} className="small" href={artifactHref(m.modelId, v)} download>v{v}</a>)}{versions.length > 10 && <span className="xs muted">+{versions.length - 10} older</span>}</span>;
        } },
      ]} />
    </Card>
  );
}
