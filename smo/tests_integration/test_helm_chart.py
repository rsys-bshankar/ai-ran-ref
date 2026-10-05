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
        # compose gives a module with a role of its own (PR-DB-2.6) that role's password file in place of db_password; the chart adopts the roles later
        assert module["database"] == any(secret.startswith("db_password") for secret in secrets), f"{name}: database access differs from the compose secrets"
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


def _secret_sources(pod: dict) -> list[dict]:
    """The Secrets a pod mounts, whether as a plain `secret` volume or as the sources of a `projected` one."""
    sources = []
    for volume in pod.get("volumes", []):
        if "secret" in volume:
            sources.append({"secretName": volume["secret"]["secretName"], **volume["secret"]})
        for source in volume.get("projected", {}).get("sources", []):
            if "secret" in source:
                sources.append({"secretName": source["secret"]["name"], **source["secret"]})
    return sources


@helm
def test_the_default_install_renders_a_deployment_per_module_and_no_rapp_mounts_the_enrollment_secret():
    docs = _render()
    deployments = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    assert set(deployments) == set(VALUES["modules"])
    for name, deployment in deployments.items():
        pod = deployment["spec"]["template"]["spec"]
        mounted = {item["path"] for source in _secret_sources(pod) for item in source.get("items", [])}
        module = _modules()[name]
        assert ("enrollment_secret" in mounted) == module["enrollment"], name
        # a module with a role of its own (PR-DB-2.6) mounts that role's password in place of the owner's
        role = module.get("databaseRole")
        assert ("db_password" in mounted) == (module["database"] and not role), name
        assert (f"db_password_{role}" in mounted) == bool(role), name
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
def test_the_pull_policy_of_the_smo_images_does_not_reach_the_database_image():
    docs = _render("--set", "image.pullPolicy=Never")
    postgres = next(d for d in docs if d["kind"] == "StatefulSet")
    assert postgres["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "IfNotPresent"
    sme = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "sme")
    assert sme["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "Never"


@helm
def test_an_external_database_drops_the_bundled_one_and_needs_a_host():
    docs = _render("--set", "postgres.enabled=false", "--set", "postgres.external.host=db.example.com", "--set", "postgres.external.sslmode=require")
    assert not [d for d in docs if d["kind"] == "StatefulSet"]
    urls = {e["value"] for d in docs if d["kind"] == "Deployment" for e in d["spec"]["template"]["spec"]["containers"][0].get("env") or [] if e["name"] == "SMO_DATABASE_URL"}
    # the owner, and Onboarding as its own role (PR-DB-2.6): the same host, the role's user
    assert "postgresql+psycopg://smo@db.example.com:5432/smo?sslmode=require" in urls and "postgresql+psycopg://smo_onboarding@db.example.com:5432/smo?sslmode=require" in urls
    assert all(u.endswith("@db.example.com:5432/smo?sslmode=require") for u in urls)
    failed = subprocess.run(["helm", "template", "smo", str(CHART), "--kube-version", "1.30.0", "--set", "postgres.enabled=false"], capture_output=True, text=True)
    assert failed.returncode != 0 and "postgres.external.host" in failed.stderr


@helm
def test_an_existing_secret_means_the_chart_makes_none():
    docs = _render("--set", "secrets.existingSecret=mine")
    assert not [d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "smo-secrets"]
    names = {src["secretName"] for d in docs if d["kind"] in ("Deployment", "StatefulSet", "Job")
             for src in _secret_sources(d["spec"].get("template", {}).get("spec", {}))}
    assert names <= {"mine", "smo-role-secrets"} and "mine" in names


@helm
def test_the_role_passwords_are_a_hook_secret_of_their_own_that_the_migrate_job_and_the_module_mount():
    docs = _render()
    roles = {m["databaseRole"] for m in _modules().values() if m.get("databaseRole")}
    assert roles >= {"onboarding", "mlmr"}
    secret = next(d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "smo-role-secrets")
    annotations = secret["metadata"]["annotations"]
    assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade" and annotations["helm.sh/hook-delete-policy"] == "before-hook-creation"
    assert int(annotations["helm.sh/hook-weight"]) < 0                       # ahead of the migrate Job, which mounts it
    assert set(secret["stringData"]) == {f"db-password-{r}" for r in roles} and all(len(v) >= 32 for v in secret["stringData"].values())
    job = next(d for d in docs if d["kind"] == "Job")
    command = job["spec"]["template"]["spec"]["containers"][0]["command"]
    assert "db_roles.py" in command[-1] and "migrate.py" in command[-1] and command[-1].index("migrate.py") < command[-1].index("db_roles.py")
    paths = {item["path"] for src in _secret_sources(job["spec"]["template"]["spec"]) for item in src["items"]}
    assert paths == {"db_password"} | {f"db_password_{r}" for r in roles}
    for d in docs:
        if d["kind"] != "Deployment" or not _modules()[d["metadata"]["name"]].get("databaseRole"):
            continue
        role = _modules()[d["metadata"]["name"]]["databaseRole"]
        pod = d["spec"]["template"]["spec"]
        env = {e["name"]: e["value"] for e in pod["containers"][0]["env"] if "value" in e}
        assert env["SMO_DATABASE_URL"] == f"postgresql+psycopg://smo_{role.replace('-', '_')}@postgres:5432/smo", role
        assert env["SMO_DATABASE_PASSWORD_FILE"] == f"/run/secrets/db_password_{role}", role
        init_env = {e["name"]: e["value"] for e in pod["initContainers"][0]["env"] if "value" in e}
        assert init_env["SMO_DATABASE_PASSWORD_FILE"] == f"/run/secrets/db_password_{role}", role    # the schema wait uses the role too


@helm
def test_with_roles_off_every_module_connects_as_the_owner_and_no_role_secret_is_made():
    docs = _render("--set", "databaseRoles.enabled=false")
    assert not [d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "smo-role-secrets"]
    for d in docs:
        if d["kind"] == "Deployment":
            pod = d["spec"]["template"]["spec"]
            env = {e["name"]: e["value"] for e in pod["containers"][0].get("env") or [] if "value" in e}
            if "SMO_DATABASE_URL" in env:
                assert env["SMO_DATABASE_URL"] == "postgresql+psycopg://smo@postgres:5432/smo" and env["SMO_DATABASE_PASSWORD_FILE"] == "/run/secrets/db_password"


@helm
def test_roles_with_an_existing_secret_are_read_from_it_and_not_made():
    docs = _render("--set", "databaseRoles.existingSecret=theirs")
    assert not [d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "smo-role-secrets"]
    names = {src["secretName"] for d in docs if d["kind"] in ("Deployment", "Job") for src in _secret_sources(d["spec"].get("template", {}).get("spec", {}))}
    assert "theirs" in names and "smo-role-secrets" not in names


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


@helm
def test_credential_delivery_is_off_by_default_and_gives_no_pod_a_service_account():
    docs = _render()
    assert not [d for d in docs if d["kind"] in ("ServiceAccount", "Role", "RoleBinding")]
    rapp_mgmt = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "rapp-mgmt")
    env = {e["name"] for e in rapp_mgmt["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert "RAPP_CREDENTIAL_DELIVERY" not in env and "serviceAccountName" not in rapp_mgmt["spec"]["template"]["spec"]


@helm
def test_credential_delivery_gives_only_rapp_mgmt_a_token_and_a_role_that_cannot_read_secrets():
    docs = _render("--set", "rappCredentials.delivery=kubernetes")
    pods = {d["metadata"]["name"]: d["spec"]["template"]["spec"] for d in docs if d["kind"] == "Deployment"}
    assert [n for n, p in pods.items() if p.get("serviceAccountName")] == ["rapp-mgmt"]
    assert all(p["automountServiceAccountToken"] is False for p in pods.values())          # the token is projected into one pod, never auto-mounted
    volume = next(v for v in pods["rapp-mgmt"]["volumes"] if v["name"] == "k8s-access")
    assert {"serviceAccountToken", "configMap"} == {k for source in volume["projected"]["sources"] for k in source}
    mounts = {m["name"]: m["mountPath"] for m in pods["rapp-mgmt"]["containers"][0]["volumeMounts"]}
    assert mounts["k8s-access"] == "/var/run/smo-k8s" and not mounts["k8s-access"].startswith(mounts["secrets"])          # not inside the secrets mount
    env = {e["name"]: e.get("value") for e in pods["rapp-mgmt"]["containers"][0]["env"]}
    assert env["RAPP_CREDENTIAL_DELIVERY"] == "kubernetes" and env["RAPP_K8S_TOKEN_FILE"] == "/var/run/smo-k8s/token"
    role = next(d for d in docs if d["kind"] == "Role")
    verbs = {v for rule in role["rules"] for v in rule["verbs"]}
    assert verbs == {"create", "update", "delete"} and role["rules"][0]["resources"] == ["secrets"]
