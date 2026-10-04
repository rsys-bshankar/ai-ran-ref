"""PR-OPS-2: the Helm chart (deploy/helm/smo) says the same as docker-compose.yml about each module, and renders.

The parity tests read the two files and need nothing else. The render tests need the `helm` binary and are skipped without it (CI's `helm` job has it).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
CHART = SMO_ROOT / "deploy" / "helm" / "smo"
COMPOSE = yaml.safe_load((SMO_ROOT / "docker-compose.yml").read_text())
VALUES = yaml.safe_load((CHART / "values.yaml").read_text())
NOT_IN_THE_CHART = {"postgres", "migrate", "netconf-lab", "edge-tls"}      # Postgres is a template of its own, migrate a Job, the other two are compose-only
helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _modules():
    defaults = VALUES["moduleDefaults"]
    return {name: {**defaults, **spec} for name, spec in VALUES["modules"].items()}


def _compose_services():
    return {name: svc for name, svc in COMPOSE["services"].items() if name not in NOT_IN_THE_CHART}


def test_every_compose_service_is_a_chart_module_and_the_other_way_round():
    assert set(_compose_services()) == set(VALUES["modules"])


def test_a_module_runs_the_image_the_release_publishes_for_it():
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_images", SMO_ROOT / "scripts" / "release_images.py")
    release_images = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release_images)
    published = {(i["context"], i["module"]): i["name"] for i in release_images.images()}
    for name, service in _compose_services().items():
        build = service["build"]
        key = (build.get("context", "."), (build.get("args") or {}).get("MODULE", ""))
        assert _modules()[name]["image"] == published[key], f"{name}: the chart runs another image than the release publishes"


def test_database_and_enrollment_follow_the_compose_secrets():
    for name, service in _compose_services().items():
        secrets = set(service.get("secrets") or [])
        module = _modules()[name]
        assert module["database"] == ("db_password" in secrets), f"{name}: database access differs from the compose secrets"
        assert module["enrollment"] == ("enrollment_secret" in secrets), f"{name}: enrollment differs from the compose secrets"


def test_an_rapp_never_has_the_enrollment_secret_and_says_it_is_an_rapp():
    for name, module in _modules().items():
        if module["env"].get("SMO_IDENTITY_KIND") == "rapp":
            assert module["enrollment"] is False, f"{name} is an rApp and must not hold the enrollment secret"
    assert {n for n, m in _modules().items() if m["env"].get("SMO_IDENTITY_KIND") == "rapp"} == {
        n for n, s in _compose_services().items() if (s.get("environment") or {}).get("SMO_IDENTITY_KIND") == "rapp"}


def test_the_workers_run_what_compose_runs():
    for name, service in _compose_services().items():
        command = service.get("command")
        assert (_modules()[name]["kind"] == "worker") == (command == ["python", "-m", "smo_shared.worker"]), name


def test_each_module_env_the_chart_sets_is_the_one_compose_sets():
    for name, service in _compose_services().items():
        compose_env = {k: str(v) for k, v in (service.get("environment") or {}).items() if k in _modules()[name]["env"]}
        for key, value in compose_env.items():
            if "${" in value:
                continue                      # an operator setting in compose, a value of the chart in the chart
            assert str(_modules()[name]["env"][key]) == value, f"{name}: {key} differs from docker-compose.yml"


def _render(*args: str) -> list[dict]:
    result = subprocess.run(["helm", "template", "smo", str(CHART), "-n", "smo", "--kube-version", "1.30.0", *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


@helm
def test_lint_is_clean():
    result = subprocess.run(["helm", "lint", str(CHART), "--kube-version", "1.30.0"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@helm
def test_the_default_install_renders_a_deployment_per_module_and_no_rapp_mounts_the_enrollment_secret():
    docs = _render()
    deployments = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    assert set(deployments) == set(VALUES["modules"])
    for name, deployment in deployments.items():
        pod = deployment["spec"]["template"]["spec"]
        mounted = {item["path"] for v in pod["volumes"] if "secret" in v for item in v["secret"].get("items", [])}
        module = _modules()[name]
        assert ("enrollment_secret" in mounted) == module["enrollment"], name
        assert ("db_password" in mounted) == module["database"], name
        container = pod["containers"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True and container["securityContext"]["capabilities"] == {"drop": ["ALL"]}, name
    assert {s["metadata"]["name"] for s in docs if s["kind"] == "Service"} >= {n for n, m in _modules().items() if m["kind"] != "worker"}


@helm
def test_no_pod_gets_a_service_account_token():
    # the token mounts under /var/run/secrets, which is inside the /run/secrets mount of a read-only root filesystem: the pod would not start
    for d in _render():
        if d["kind"] in ("Deployment", "StatefulSet", "Job"):
            assert d["spec"]["template"]["spec"]["automountServiceAccountToken"] is False, d["metadata"]["name"]


@helm
def test_every_module_with_a_database_waits_for_the_schema():
    for d in _render():
        if d["kind"] != "Deployment":
            continue
        pod = d["spec"]["template"]["spec"]
        init = [c["name"] for c in pod.get("initContainers", [])]
        assert ("wait-for-schema" in init) == _modules()[d["metadata"]["name"]]["database"], d["metadata"]["name"]


@helm
def test_install_runs_the_migration_as_a_job_and_upgrade_as_a_pre_upgrade_hook():
    install = [d for d in _render() if d["kind"] == "Job"]
    assert [j["metadata"]["name"] for j in install] == ["migrate"] and "annotations" not in install[0]["metadata"]
    result = subprocess.run(["helm", "template", "smo", str(CHART), "-n", "smo", "--kube-version", "1.30.0", "--is-upgrade"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    upgrade = [d for d in yaml.safe_load_all(result.stdout) if d and d["kind"] == "Job"]
    assert len(upgrade) == 1 and upgrade[0]["metadata"]["annotations"]["helm.sh/hook"] == "pre-upgrade"


@helm
def test_an_external_database_drops_the_bundled_one_and_needs_a_host():
    docs = _render("--set", "postgres.enabled=false", "--set", "postgres.external.host=db.example.com", "--set", "postgres.external.sslmode=require")
    assert not [d for d in docs if d["kind"] == "StatefulSet"]
    urls = {e["value"] for d in docs if d["kind"] == "Deployment" for e in d["spec"]["template"]["spec"]["containers"][0].get("env") or [] if e["name"] == "SMO_DATABASE_URL"}
    assert urls == {"postgresql+psycopg://smo@db.example.com:5432/smo?sslmode=require"}
    failed = subprocess.run(["helm", "template", "smo", str(CHART), "--kube-version", "1.30.0", "--set", "postgres.enabled=false"], capture_output=True, text=True)
    assert failed.returncode != 0 and "postgres.external.host" in failed.stderr


@helm
def test_an_existing_secret_means_the_chart_makes_none():
    docs = _render("--set", "secrets.existingSecret=mine")
    assert not [d for d in docs if d["kind"] == "Secret"]
    assert all(v["secret"]["secretName"] == "mine" for d in docs if d["kind"] in ("Deployment", "StatefulSet", "Job")
               for v in d["spec"].get("template", {}).get("spec", {}).get("volumes", []) if "secret" in v)


@helm
def test_the_optional_templates_render_when_switched_on():
    docs = _render("--set", "networkPolicy.enabled=true", "--set", "podDisruptionBudget.enabled=true", "--set", "autoscaling.enabled=true",
                   "--set", "ingress.enabled=true", "--set", "ingress.gui.host=gui.example.com", "--set", "ingress.r1.host=r1.example.com",
                   "--set", "ingress.r1.tlsSecretName=r1-tls", "--set", "ingress.r1.publicBaseUrl=https://r1.example.com")
    kinds = {d["kind"] for d in docs}
    assert {"NetworkPolicy", "PodDisruptionBudget", "HorizontalPodAutoscaler", "Ingress"} <= kinds
    policy = next(d for d in docs if d["kind"] == "NetworkPolicy")
    assert policy["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"] == "a1-related" and policy["spec"]["egress"] == []
    r1 = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "r1-termination")
    assert {"name": "R1_PUBLIC_BASE_URL", "value": "https://r1.example.com"} in r1["spec"]["template"]["spec"]["containers"][0]["env"]
    assert not [d for d in docs if d["kind"] == "PodDisruptionBudget" and d["metadata"]["name"] in ("onboarding", "gui-bff")]     # a volume holds one pod
