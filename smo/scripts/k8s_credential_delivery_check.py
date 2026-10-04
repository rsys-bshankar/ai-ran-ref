"""Run INSIDE the rApp Management pod of a Helm install with `rappCredentials.delivery=kubernetes` (CI's `helm` job):

    kubectl -n smo exec -i deploy/rapp-mgmt -- python3 - < scripts/k8s_credential_delivery_check.py

It uses the pod's real service account against the real API server: writes an instance's Secret, rotates it, shows the pod cannot read Secrets back (the
Role has no get), and deletes it. The Secret's content is checked from outside by the CI step with kubectl, between `deliver` and `withdraw`.
"""
import sys

from smo_shared import credential_delivery as cd

step = sys.argv[1] if len(sys.argv) > 1 else "all"
failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        failures.append(name)


if step in ("deliver", "all"):
    check("delivery is configured for Kubernetes", cd.mode() == "kubernetes")
    first = cd.deliver("ci-check", "api-invoker-ci-1", "secret-one")
    check("the Secret is written", first == {"kubernetesSecret": "rapp-ci-check-credentials"}, first)
    second = cd.deliver("ci-check", "api-invoker-ci-2", "secret-two")        # rotation: the Secret exists, so it is replaced
    check("rotating replaces it", second == {"kubernetesSecret": "rapp-ci-check-credentials"}, second)
    client, namespace = cd._client()
    with client:
        read = client.get(f"/api/v1/namespaces/{namespace}/secrets/smo-secrets")
        check("the pod cannot read the platform's own Secret back (403)", read.status_code == 403, read.status_code)
        read = client.get(f"/api/v1/namespaces/{namespace}/secrets/rapp-ci-check-credentials")
        check("nor the one it just wrote (403)", read.status_code == 403, read.status_code)
if step in ("withdraw", "all"):
    check("the Secret is deleted", cd.withdraw("ci-check") == "DONE")
    check("deleting it again is done", cd.withdraw("ci-check") == "DONE")
sys.exit(1 if failures else 0)
