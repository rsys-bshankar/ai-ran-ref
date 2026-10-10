/** Section `aiml.artifacts`: the selected model's artifact versions (download links through the BFF), the artifact upload (`POST
 * /mlmr/models/{id}/artifact`, multipart) and, once the model is CERTIFIED or PROMOTED, the MLLF "deploy to node groups" form. Both forms are
 * role-gated with `Can`. Reads the same model and lifecycle queries as the detail panel (shared cache, no extra call). */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import { Can, Card, Field } from "../../../components/ui";
import { DEPLOYABLE_MODEL_STATES, splitList } from "../../../lib/domain";
import { Skeleton } from "../../../kit/states";
import { artifactVersions } from "../data/board";
import { artifactHref, mllfModel, mlmrModel, useModel, useModelLifecycle } from "../data/queries";

/** The box. */
export function ModelArtifacts({ id }: { id: string }) {
  const model = useModel(id);
  const lifecycle = useModelLifecycle(id);
  const m = model.data;
  const l = lifecycle.data;
  const versions = artifactVersions(m?.artifactLocation, id);
  return (
    <Card section="aiml.artifacts" title="Artifacts & deployment" sub="MLMR artifact store · MLLF node groups">
      {!m ? <Skeleton lines={2} /> : <>
        {versions.length > 0 ? (
          <ul className="plain-list">
            {versions.map((v) => <li key={v}><a href={artifactHref(id, v)} download>Download artifact v{v}</a></li>)}
          </ul>
        ) : <p className="muted">No artifact uploaded.</p>}
        <Can method="POST" path={`${mlmrModel(id)}/artifact`}><ArtifactUpload modelId={id} /></Can>
        {l && DEPLOYABLE_MODEL_STATES.includes(l.modelLifecycleState) && (
          <Can method="POST" path={`${mllfModel(id)}/deploy`}><DeployNodeGroups modelId={id} clearedNodeGroups={l.clearedNodeGroups} /></Can>
        )}
      </>}
    </Card>
  );
}

/** Upload one .zip as the model's next artifact version. */
function ArtifactUpload({ modelId }: { modelId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const action = useSmoAction();
  return (
    <form className="form inline" onSubmit={(e) => {
      e.preventDefault();
      if (!file) return;
      const body = new FormData();
      body.append("file", file);
      action.mutate({ method: "POST", path: `${mlmrModel(modelId)}/artifact`, body, success: `Uploaded ${file.name}` });
    }}>
      <Field label="Upload artifact (.zip)"><input type="file" accept=".zip,application/zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
      <button type="submit" className="btn" disabled={!file || action.isPending}>Upload</button>
    </form>
  );
}

/** Clear the model for deployment to node groups (MLLF stamps them on AIMgF's lifecycle row). */
function DeployNodeGroups({ modelId, clearedNodeGroups }: { modelId: string; clearedNodeGroups: string[] }) {
  const [groups, setGroups] = useState(clearedNodeGroups.join(", "));
  const action = useSmoAction();
  return (
    <form className="form inline" onSubmit={(e) => {
      e.preventDefault();
      action.mutate({ method: "POST", path: `${mllfModel(modelId)}/deploy`, json: splitList(groups), success: "Deployment targets cleared (MLLF)" });
    }}>
      <Field label="Deploy to node groups" hint="Comma-separated. Stamps clearedNodeGroups on AIMgF's own lifecycle row; requires CERTIFIED or PROMOTED."><input value={groups} onChange={(e) => setGroups(e.target.value)} placeholder="edge-gpu-a, edge-gpu-b" /></Field>
      <button type="submit" className="btn" disabled={!splitList(groups).length || action.isPending}>Deploy</button>
    </form>
  );
}
