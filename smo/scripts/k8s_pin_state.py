#!/usr/bin/env python3
"""A Helm post-renderer for the high-availability lane (PR-V-12): puts the pods that hold state on one node.

    helm install ... --post-renderer scripts/k8s_pin_state.py            (reads the rendered manifests on stdin, writes them on stdout)

The lane drains a worker, so it needs a worker that holds nothing that cannot move: Postgres and the two single-pod deployments with a volume (onboarding's package store, the
GUI backend's SQLite file) are tied to the node their volume was made on, and where the scheduler puts them is a matter of chance, so on a bad run all three workers held one and
nothing could be drained. This adds `nodeSelector: {smo-state: "true"}` to those three; the lane labels one worker with it before installing. The chart itself is untouched.
"""

import sys

import yaml

STATE = {("StatefulSet", "postgres"), ("Deployment", "onboarding"), ("Deployment", "gui-bff")}
LABEL = {"smo-state": "true"}


def pin(documents: list) -> list:
    """Adds `nodeSelector: {smo-state: "true"}` to the pod template of each StatefulSet or Deployment named in `STATE`, in place, and returns the documents.

        Other documents (including non-mapping ones) pass through untouched; an existing nodeSelector keeps its other keys.
    """
    for doc in documents:
        if isinstance(doc, dict) and (doc.get("kind"), (doc.get("metadata") or {}).get("name")) in STATE:
            doc["spec"]["template"]["spec"].setdefault("nodeSelector", {}).update(LABEL)
    return documents


def main() -> int:
    """Helm post-renderer entry: reads the rendered manifests (YAML stream) on stdin, writes them back with the pins applied, returns 0. Key order is preserved."""
    documents = [d for d in yaml.safe_load_all(sys.stdin.read()) if d is not None]
    sys.stdout.write(yaml.safe_dump_all(pin(documents), sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
