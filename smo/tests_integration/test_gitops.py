"""The GitOps example (PR-OPS-6, deploy/gitops/): the overlays and the Argo CD Applications are well-formed and agree with the chart.

Structural, so it runs everywhere: every YAML parses, every path a file names exists, and every key an overlay's values set exists in the chart's own
values.yaml (a typo would otherwise be accepted by Helm and silently do nothing). With `kustomize` and `helm` installed each overlay is also built.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
GITOPS = SMO_ROOT / "deploy" / "gitops"
CHART = SMO_ROOT / "deploy" / "helm" / "smo"
CHART_VALUES = yaml.safe_load((CHART / "values.yaml").read_text())
OVERLAYS = sorted(p for p in (GITOPS / "overlays").iterdir() if p.is_dir())
ARGOCD = sorted((GITOPS / "argocd").glob("*.yaml"))
REPO_SMO_PREFIX = "smo/"      # the repository root is one level above smo/; an Argo CD path starts there


def _load(path: Path):
    return yaml.safe_load(path.read_text())


def _missing_keys(values: dict, schema: dict, prefix: str = "") -> list[str]:
    """Keys in `values` that the chart's values do not have. A map the chart leaves empty (env: {}, annotations: {}) takes any key."""
    problems = []
    for key, value in values.items():
        here = f"{prefix}{key}"
        if key not in schema:
            problems.append(here)
        elif isinstance(value, dict) and isinstance(schema[key], dict) and schema[key]:
            problems += _missing_keys(value, schema[key], here + ".")
    return problems


def test_there_are_a_lab_a_staging_and_a_production_overlay_and_two_applications():
    assert [p.name for p in OVERLAYS] == ["lab", "prod", "staging"]
    assert [p.name for p in ARGOCD] == ["application-kustomize.yaml", "application.yaml"]


def test_the_base_resources_exist():
    kustomization = _load(GITOPS / "base" / "kustomization.yaml")
    assert kustomization["kind"] == "Kustomization"
    for resource in kustomization["resources"]:
        assert (GITOPS / "base" / resource).is_file(), resource
        assert _load(GITOPS / "base" / resource)["kind"] == "Namespace"


@pytest.mark.parametrize("overlay", OVERLAYS, ids=lambda p: p.name)
def test_an_overlay_names_the_base_the_chart_and_a_values_file_that_exist(overlay):
    kustomization = _load(overlay / "kustomization.yaml")
    assert kustomization["kind"] == "Kustomization"
    for resource in kustomization["resources"]:
        assert (overlay / resource / "kustomization.yaml").is_file(), resource
    chart_home = (overlay / kustomization["helmGlobals"]["chartHome"]).resolve()
    for chart in kustomization["helmCharts"]:
        assert (chart_home / chart["name"] / "Chart.yaml").is_file(), f"chart {chart['name']} is not under {chart_home}"
        assert (chart_home / chart["name"]).resolve() == CHART.resolve()
        assert (overlay / chart["valuesFile"]).is_file()
        assert chart["namespace"] == "smo"


@pytest.mark.parametrize("overlay", OVERLAYS, ids=lambda p: p.name)
def test_every_key_an_overlay_sets_exists_in_the_charts_values(overlay):
    values = _load(overlay / "values.yaml")
    assert values, "an overlay with no values is the chart's defaults, which is not an overlay"
    modules = values.pop("modules", {})
    assert _missing_keys(values, CHART_VALUES) == []
    for module, settings in modules.items():
        assert module in CHART_VALUES["modules"], f"modules.{module} is not a module of the chart"
        # a module's settings are laid over moduleDefaults, so any key of either is a real setting
        assert _missing_keys(settings, {**CHART_VALUES["moduleDefaults"], **CHART_VALUES["modules"][module]}, f"modules.{module}.") == []


@pytest.mark.parametrize("overlay", OVERLAYS, ids=lambda p: p.name)
def test_an_overlay_carries_no_credential(overlay):
    def keys(node):
        for key, value in (node.items() if isinstance(node, dict) else []):
            yield str(key)
            yield from keys(value)

    for key in keys(_load(overlay / "values.yaml")):
        assert key.lower() not in ("password", "secret", "token", "apikey", "dbpassword"), \
            f"{overlay.name}: a value named {key!r}; secrets come from a Secret named by existingSecret"


@pytest.mark.parametrize("application", ARGOCD, ids=lambda p: p.name)
def test_an_argo_application_points_at_paths_of_this_repository(application):
    doc = _load(application)
    assert doc["apiVersion"] == "argoproj.io/v1alpha1" and doc["kind"] == "Application"
    source = doc["spec"]["source"]
    assert source["path"].startswith(REPO_SMO_PREFIX), "the repository root is above smo/"
    path = SMO_ROOT / source["path"].removeprefix(REPO_SMO_PREFIX)
    assert path.is_dir(), source["path"]
    assert doc["spec"]["destination"]["namespace"] == "smo"
    if "helm" in source:
        assert (path / "Chart.yaml").is_file()
        assert path.resolve() == CHART.resolve()
        for value_file in source["helm"]["valueFiles"]:
            assert (path / value_file).resolve().is_file(), f"{value_file} is not a file relative to the chart"
            assert (path / value_file).resolve().is_relative_to(GITOPS.resolve())
    else:
        assert (path / "kustomization.yaml").is_file()
        assert path.resolve().is_relative_to((GITOPS / "overlays").resolve())


@pytest.mark.skipif(shutil.which("kustomize") is None or shutil.which("helm") is None, reason="kustomize and helm are not installed")
@pytest.mark.parametrize("overlay", OVERLAYS, ids=lambda p: p.name)
def test_an_overlay_builds_with_kustomize(overlay):
    result = subprocess.run(["kustomize", "build", "--enable-helm", str(overlay)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    kinds = {d["kind"] for d in docs}
    assert {"Namespace", "Deployment", "Service", "Job"} <= kinds
    assert {d["metadata"]["name"] for d in docs if d["kind"] == "Deployment"} >= {"r1-termination", "sme", "gui"}
    if overlay.name != "lab":
        assert "Ingress" in kinds and "PodDisruptionBudget" in kinds
        assert not [d for d in docs if d["kind"] == "StatefulSet"], "an external database drops the bundled one"
