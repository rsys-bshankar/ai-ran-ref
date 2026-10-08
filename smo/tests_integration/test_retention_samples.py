"""DB-3.10: the retention periods proposed in docs/RETENTION.md ship in a production sample values file (Helm) and in .env.example (compose);
the code and the chart defaults keep every row, so an upgrade deletes nothing.

Plain YAML and text, no `helm` needed; the rendered checks are skipped without it (CI's `helm` job has it).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
CHART = SMO_ROOT / "deploy" / "helm" / "smo"
PRODUCTION = yaml.safe_load((CHART / "values-production.yaml").read_text())
DEFAULTS = yaml.safe_load((CHART / "values.yaml").read_text())
GITOPS_PROD = yaml.safe_load((SMO_ROOT / "deploy" / "gitops" / "overlays" / "prod" / "values.yaml").read_text())
ENV_EXAMPLE = (SMO_ROOT / ".env.example").read_text()
COMPOSE = (SMO_ROOT / "docker-compose.yml").read_text()
RETENTION_MD = (SMO_ROOT / "docs" / "RETENTION.md").read_text()
helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")

# the settings in days that docs/RETENTION.md proposes a value for, and where the chart puts each (module env; "gui" is gui.env)
PROPOSED = {"SMO_RETENTION_ALARMS_DAYS": "90", "SMO_RETENTION_PM_FILES_DAYS": "14", "SAFEGUARD_REFUSAL_RETENTION_DAYS": "90",
            "SMO_RETENTION_MDAF_REPORTS_DAYS": "30", "GUI_AUDIT_RETENTION_DAYS": "365"}
WHERE = {"SMO_RETENTION_ALARMS_DAYS": ["ran-nf-oam-worker"], "SMO_RETENTION_PM_FILES_DAYS": ["ran-nf-oam-worker"],
         "SAFEGUARD_REFUSAL_RETENTION_DAYS": ["ran-nf-oam", "ran-nf-oam-worker"], "SMO_RETENTION_MDAF_REPORTS_DAYS": ["mdaf-worker"],
         "GUI_AUDIT_RETENTION_DAYS": ["gui"]}


def _table() -> dict[str, str]:
    """The days-settings of the table in docs/RETENTION.md with their 'Proposed value' (a row names one setting in backticks in its fifth column)."""
    found = {}
    for line in RETENTION_MD.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 8:
            continue
        match = re.match(r"`([A-Z_]+_DAYS)`", cells[4])
        if match:
            found[match.group(1)] = cells[5]
    return found


def _env(values: dict, where: str) -> dict:
    return values["gui"]["env"] if where == "gui" else values["modules"][where]["env"]


def test_the_document_proposes_the_values_the_samples_carry():
    assert _table() == PROPOSED, "docs/RETENTION.md and the production sample disagree about the days-settings or their proposed values"


def test_the_production_sample_sets_every_days_setting_of_the_document_to_the_proposed_value():
    for variable, value in PROPOSED.items():
        for where in WHERE[variable]:
            assert _env(PRODUCTION, where)[variable] == value, f"{where}: {variable}"


def test_the_production_sample_only_names_modules_the_chart_has_and_sets_nothing_but_retention():
    assert set(PRODUCTION["modules"]) <= set(DEFAULTS["modules"])
    for name, module in PRODUCTION["modules"].items():
        assert set(module) == {"env"}, f"{name}: the retention sample sets only env"
        assert all("RETENTION" in key for key in module["env"]), name
    assert set(PRODUCTION) == {"modules", "gui"} and set(PRODUCTION["gui"]) == {"env"}


def test_the_chart_defaults_keep_every_row():
    seen = 0
    for name, module in DEFAULTS["modules"].items():
        for key, value in (module.get("env") or {}).items():
            if "RETENTION" in key:
                seen += 1
                assert str(value) == "0", f"{name}: {key} defaults to {value}: an upgrade would delete rows"
    for key in DEFAULTS["gui"]["env"] or {}:
        assert "RETENTION" not in key, f"gui.env defaults set {key}"
    assert seen >= 5


def test_the_gitops_production_overlay_carries_the_same_periods():
    for variable, value in PROPOSED.items():
        for where in WHERE[variable]:
            assert _env(GITOPS_PROD, where)[variable] == value, f"overlays/prod, {where}: {variable}"


def test_the_gui_audit_log_is_not_given_a_cronjob_it_could_not_run_safely():
    """gui-bff keeps SQLite on a volume one pod may hold (Recreate): a second pod from a CronJob must not open it. The sample says how to exec instead."""
    assert not (CHART / "templates" / "cronjob.yaml").exists()
    assert not any("CronJob" in p.read_text() for p in (CHART / "templates").glob("*.yaml"))
    assert DEFAULTS["modules"]["gui-bff"]["persistence"]
    assert "kubectl -n smo exec deploy/gui-bff -- python -m app.retention" in (CHART / "values-production.yaml").read_text()


def test_env_example_has_every_retention_variable_of_the_document_with_the_proposed_value():
    for variable, value in PROPOSED.items():
        assert re.search(rf"^# {variable}={value}\b", ENV_EXAMPLE, re.M), f".env.example: {variable}={value}"
    assert re.search(r"^# SMO_RETENTION_WARN_ROWS=1000000\b", ENV_EXAMPLE, re.M)
    assert not re.search(r"^[A-Z_]*RETENTION[A-Z_]*=", ENV_EXAMPLE, re.M), "the retention variables are commented out: .env.example deletes nothing"


def test_compose_passes_every_retention_variable_through_with_a_default_that_keeps_everything():
    for variable in PROPOSED:
        assert re.search(rf"{variable}: \$\{{{variable}:-0\}}", COMPOSE), variable
    assert re.search(r"SMO_RETENTION_WARN_ROWS: \$\{SMO_RETENTION_WARN_ROWS:-1000000\}", COMPOSE)


def _render(*args: str) -> list[dict]:
    result = subprocess.run(["helm", "template", "smo", str(CHART), "-n", "smo", "--kube-version", "1.30.0", *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _deployment_env(docs: list[dict], name: str) -> dict:
    deployment = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == name)
    return {e["name"]: e.get("value") for e in deployment["spec"]["template"]["spec"]["containers"][0].get("env") or []}


@helm
def test_the_production_sample_renders_and_sets_the_retention_of_the_workers_and_the_gui_backend():
    docs = _render("-f", str(CHART / "values-production.yaml"))
    for variable, value in PROPOSED.items():
        for where in WHERE[variable]:
            name = "gui-bff" if where == "gui" else where
            assert _deployment_env(docs, name)[variable] == value, f"{name}: {variable}"
    assert not [d for d in docs if d["kind"] == "CronJob"]


@helm
def test_the_default_render_sets_no_retention_other_than_zero():
    docs = _render()
    for doc in docs:
        if doc["kind"] != "Deployment":
            continue
        for key, value in _deployment_env(docs, doc["metadata"]["name"]).items():
            if "RETENTION" in key:
                assert value == "0", f"{doc['metadata']['name']}: {key}={value}"
