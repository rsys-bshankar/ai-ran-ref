"""scripts/k8s_pin_state.py: the post-renderer of the high-availability lane pins the three pods that hold state, and nothing else."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("k8s_pin_state", SMO_ROOT / "scripts" / "k8s_pin_state.py")
p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p)


def workload(kind, name, selector=None):
    spec = {"containers": []}
    if selector:
        spec["nodeSelector"] = selector
    return {"kind": kind, "metadata": {"name": name}, "spec": {"template": {"spec": spec}}}


def test_only_postgres_onboarding_and_the_gui_backend_are_pinned():
    docs = [workload("StatefulSet", "postgres"), workload("Deployment", "onboarding"), workload("Deployment", "gui-bff"),
            workload("Deployment", "sme"), workload("Deployment", "postgres"), {"kind": "Service", "metadata": {"name": "postgres"}}]
    out = p.pin(docs)
    pinned = {(d["kind"], d["metadata"]["name"]) for d in out if d["kind"] != "Service" and d["spec"]["template"]["spec"].get("nodeSelector")}
    assert pinned == {("StatefulSet", "postgres"), ("Deployment", "onboarding"), ("Deployment", "gui-bff")}


def test_an_existing_selector_is_kept_and_extended():
    out = p.pin([workload("Deployment", "onboarding", {"disk": "ssd"})])
    assert out[0]["spec"]["template"]["spec"]["nodeSelector"] == {"disk": "ssd", "smo-state": "true"}


def test_it_works_as_a_filter_on_a_multi_document_stream():
    text = yaml.safe_dump_all([workload("StatefulSet", "postgres"), workload("Deployment", "sme")])
    done = subprocess.run([sys.executable, str(SMO_ROOT / "scripts" / "k8s_pin_state.py")], input=text, capture_output=True, text=True, check=True)
    docs = list(yaml.safe_load_all(done.stdout))
    assert len(docs) == 2 and docs[0]["spec"]["template"]["spec"]["nodeSelector"] == {"smo-state": "true"}
    assert "nodeSelector" not in docs[1]["spec"]["template"]["spec"]
