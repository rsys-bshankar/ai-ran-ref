"""Delivering an rApp instance's credentials to its workload (PR-SEC-14, completing it).

SME keeps only a hash of an invoker's secret, so the secret exists in one place: the moment rApp Management registers the instance's invoker. Until now
it was thrown away at create and could be fetched once, by a person, from `POST /instances/{id}/credentials`, and then had to be put into the workload
by hand. With `RAPP_CREDENTIAL_DELIVERY=kubernetes` rApp Management writes it straight into a Kubernetes Secret, `rapp-<instanceId>-credentials`, in its
own namespace, and the workload (which NFO deploys) takes it from there:

    envFrom:
      - secretRef: { name: rapp-<instanceId>-credentials }       # SMO_INVOKER_ID, SMO_INVOKER_SECRET, SMO_IDENTITY_KIND=rapp

The secret is written when the invoker is made (at create, and again when the credentials are rotated), replaced on rotation, and deleted when the
instance is terminated. No person ever sees it and it is not in rApp Management's database. In this mode the credentials endpoint answers with the
Secret's name instead of the secret.

The API server is reached as Kubernetes tells a pod to (`KUBERNETES_SERVICE_HOST`/`PORT`, a projected service-account token and the cluster CA); the
Helm chart sets that up for the rApp Management pod only (`rappCredentials.delivery=kubernetes`), with a Role that can create, update and delete Secrets in
the release's namespace and read none. `RAPP_CREDENTIAL_DELIVERY=none` (the default, and the only mode outside Kubernetes) does nothing.
"""

import logging
import os

import httpx

log = logging.getLogger(__name__)

MODE_ENV = "RAPP_CREDENTIAL_DELIVERY"
LABEL = "app.kubernetes.io/managed-by"
MANAGER = "smo-rapp-mgmt"


class DeliveryFailed(RuntimeError):
    """The credentials could not be written to (or removed from) their destination."""


def mode(environ=os.environ) -> str:
    value = environ.get(MODE_ENV, "none").strip().lower()
    return value if value in ("none", "kubernetes") else "none"


def object_name(instance_id) -> str:
    return f"rapp-{instance_id}-credentials"


def _client(environ=os.environ) -> tuple[httpx.Client, str]:
    host, port = environ.get("KUBERNETES_SERVICE_HOST"), environ.get("KUBERNETES_SERVICE_PORT", "443")
    namespace = environ.get("RAPP_K8S_NAMESPACE")
    token_file, ca_file = environ.get("RAPP_K8S_TOKEN_FILE"), environ.get("RAPP_K8S_CA_FILE")
    if not (host and namespace and token_file and ca_file):
        raise DeliveryFailed("credential delivery is on but this pod has no Kubernetes access configured "
                             "(KUBERNETES_SERVICE_HOST, RAPP_K8S_NAMESPACE, RAPP_K8S_TOKEN_FILE, RAPP_K8S_CA_FILE)")
    try:
        with open(token_file) as f:                       # re-read on every call: a projected token is rotated by the kubelet
            token = f.read().strip()
    except OSError as exc:
        raise DeliveryFailed(f"cannot read the service-account token: {exc}") from exc
    host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return httpx.Client(base_url=f"https://{host}:{port}", headers={"Authorization": f"Bearer {token}"}, verify=ca_file, timeout=10.0), namespace


def deliver(instance_id, invoker_id: str, secret: str, environ=os.environ, client_factory=None) -> dict | None:
    """Writes the instance's credentials to its Secret (created, or replaced when it exists). None when delivery is off; else {"kubernetesSecret": name}."""
    if mode(environ) != "kubernetes":
        return None
    target = object_name(instance_id)
    body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": target, "labels": {LABEL: MANAGER, "smo/rapp-instance": str(instance_id)}},
            "stringData": {"SMO_INVOKER_ID": invoker_id, "SMO_INVOKER_SECRET": secret, "SMO_IDENTITY_KIND": "rapp"}}
    client, namespace = (client_factory or _client)(environ)
    try:
        with client:
            resp = client.post(f"/api/v1/namespaces/{namespace}/secrets", json=body)
            if resp.status_code == 409:                   # rotated: replace what is there
                resp = client.put(f"/api/v1/namespaces/{namespace}/secrets/{target}", json=body)
            if resp.status_code not in (200, 201):
                raise DeliveryFailed(f"the Kubernetes API answered {resp.status_code} writing the credentials object {target}")
    except httpx.HTTPError as exc:
        raise DeliveryFailed(f"the Kubernetes API could not be reached: {exc}") from exc
    return {"kubernetesSecret": target}


def withdraw(instance_id, environ=os.environ, client_factory=None) -> str:
    """Deletes the instance's Secret; a Secret that is already gone is done. Returns "DONE", "SKIPPED: ..." or "FAILED: ..." (it never raises: teardown goes on)."""
    if mode(environ) != "kubernetes":
        return "SKIPPED: credential delivery is off"
    target = object_name(instance_id)
    try:
        client, namespace = (client_factory or _client)(environ)
        with client:
            resp = client.delete(f"/api/v1/namespaces/{namespace}/secrets/{target}")
        return "DONE" if resp.status_code in (200, 202, 404) else f"FAILED: the Kubernetes API answered {resp.status_code}"
    except (DeliveryFailed, httpx.HTTPError) as exc:
        log.warning("could not delete the credentials object of instance %s (%s)", instance_id, type(exc).__name__)
        return f"FAILED: {exc}"
