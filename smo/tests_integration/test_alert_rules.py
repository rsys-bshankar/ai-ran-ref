"""PR-OBS-5 / PR-OBS-7: the Prometheus rules, their runbooks and the SLO document agree with each other and with the code.

`promtool` is not assumed: the structural checks below are the ones `promtool check rules` makes that can go wrong in this file (shape, names, durations,
balanced PromQL, template braces), plus three that it cannot make: every metric (and label) a rule uses is one the code exports, every alert's
`runbook_url` is an existing page, and every SLO alert is in `docs/SLOS.md`. With `promtool` on the PATH it is run as well.
"""

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
CHART = SMO_ROOT / "deploy" / "helm" / "smo"
RULES_FILE = CHART / "files" / "smo-alerts.rules.yaml"
RUNBOOKS = SMO_ROOT / "docs" / "runbooks"
SLOS = SMO_ROOT / "docs" / "SLOS.md"
RUNBOOK_URL_PREFIX = "https://github.com/rsys-bshankar/ai-ran-ref/blob/main/smo/docs/runbooks/"
RULES = yaml.safe_load(RULES_FILE.read_text())

NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
DURATION = re.compile(r"^(\d+(ms|s|m|h|d|w|y))+$")
SEVERITIES = {"info", "warning", "critical"}
PROMQL_WORDS = {"and", "or", "unless", "bool", "offset", "inf", "nan", "sum", "min", "max", "avg", "count", "topk", "bottomk"}
SCRAPE_LABELS = {"job", "instance", "le", "quantile"}                  # added by Prometheus or the histogram, not by the code
SECTIONS = ("Symptom", "Impact", "Diagnosis", "Mitigation", "Escalation")
# Series that are not the code's but the CloudNativePG operator's, read from each instance's :9187. The CI job `cnpg-wal-archive` (.github/workflows/smo-dr.yml) fails unless a real
# instance exports every name listed here, so they are checked against the operator, not against this file.
OPERATOR_METRICS = {"cnpg_collector_last_available_backup_timestamp", "cnpg_pg_stat_archiver_seconds_since_last_archival", "cnpg_pg_stat_archiver_failed_count"}


def _rules():
    return [rule for group in RULES["groups"] for rule in group["rules"]]


def _alerts():
    return [rule for rule in _rules() if "alert" in rule]


def _recordings():
    return [rule for rule in _rules() if "record" in rule]


def _exported_metrics() -> dict[str, set[str]]:
    """Every `smo_*` series the code defines, with its label names: from the constructors (Counter, Histogram, Gauge, GaugeMetricFamily and
    `register_query_gauge`) found by parsing the non-test sources. A histogram also exports _bucket, _sum and _count."""
    found: dict[str, set[str]] = {}
    constructors = {"Counter", "Histogram", "Gauge", "GaugeMetricFamily", "register_query_gauge"}
    for path in SMO_ROOT.rglob("*.py"):
        if any(part in {"tests", "tests_integration", "node_modules", ".venv"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                continue
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            name = node.args[0].value
            if callee not in constructors or not name.startswith("smo_"):
                continue
            labels_node = node.args[2] if len(node.args) > 2 else next((k.value for k in node.keywords if k.arg in ("labels", "labelnames")), None)
            labels = {e.value for e in labels_node.elts if isinstance(e, ast.Constant)} if isinstance(labels_node, ast.List) else set()
            found[name] = labels
            if callee == "Histogram":
                for suffix in ("_bucket", "_sum", "_count"):
                    found[name + suffix] = labels | {"le"}
    return found


def _strip(expr: str) -> str:
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', expr)


def _selectors(expr: str):
    """(metric name, label names used in its `{...}`) for every series selector of `expr`: what is left once strings, range windows, grouping
    clauses, function names and keywords are taken out."""
    body = re.sub(r"\[[^\]]*\]", "", _strip(expr))
    body = re.sub(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", body)
    for match in re.finditer(r"(?<![\w:.])([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?", body):
        name, block = match.group(1), match.group(2)
        if name in PROMQL_WORDS or re.match(r"\s*\(", body[match.end():]):
            continue                                                                    # a keyword, or a function / aggregation call
        yield name, set(re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=~|!=|!~|=)", block or ""))


def _balanced(text: str) -> bool:
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for char in _strip(text):
        if char in "([{":
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
    return not stack


# --- structure ---------------------------------------------------------------------------------------------------------------------

def test_the_file_is_a_prometheus_rule_file_with_named_groups_of_rules():
    assert set(RULES) == {"groups"} and RULES["groups"]
    names = [group["name"] for group in RULES["groups"]]
    assert len(names) == len(set(names)) and all(group["rules"] for group in RULES["groups"])
    for group in RULES["groups"]:
        assert set(group) <= {"name", "interval", "rules"}
        assert "interval" not in group or DURATION.match(group["interval"])


def test_every_rule_is_a_record_or_an_alert_with_a_valid_name_and_expression():
    names = []
    for rule in _rules():
        assert ("record" in rule) != ("alert" in rule), rule
        name = rule.get("record") or rule["alert"]
        assert NAME.match(name), name
        assert isinstance(rule["expr"], str) and rule["expr"].strip(), name
        assert _balanced(rule["expr"]), f"{name}: unbalanced brackets in {rule['expr']!r}"
        assert set(rule) <= {"record", "alert", "expr", "for", "keep_firing_for", "labels", "annotations"}, name
        for key in ("for", "keep_firing_for"):
            assert key not in rule or DURATION.match(str(rule[key])), f"{name}: {key}={rule[key]!r}"
        for key, value in {**rule.get("labels", {}), **rule.get("annotations", {})}.items():
            assert LABEL.match(key) and isinstance(value, str), (name, key)
        names.append(name)
    assert len(names) == len(set(names)), "a rule name is used twice"


def test_a_recording_rule_is_named_level_metric_operations_and_is_not_labelled_as_an_alert():
    for rule in _recordings():
        assert rule["record"].startswith("smo:") and rule["record"].count(":") >= 2, rule["record"]
        assert "annotations" not in rule and "for" not in rule


def test_every_alert_has_a_known_severity_a_summary_a_description_and_a_runbook_url():
    for rule in _alerts():
        assert rule["labels"]["severity"] in SEVERITIES, rule["alert"]
        for key in ("summary", "description", "runbook_url"):
            assert rule["annotations"].get(key), (rule["alert"], key)
        for key in ("summary", "description"):                                                       # template braces are paired and use known roots
            text = rule["annotations"][key]
            assert text.count("{{") == text.count("}}"), (rule["alert"], key)
            for expression in re.findall(r"\{\{(.*?)\}\}", text):
                assert re.match(r"\s*\$(labels\.\w+|value)(\s*\|\s*\w+)?\s*$", expression), (rule["alert"], expression)


def test_an_slo_burn_alert_is_a_pair_of_a_fast_and_a_slow_window_and_the_labels_agree():
    by_slo: dict[str, list[str]] = {}
    for rule in _alerts():
        if "slo" in rule["labels"]:
            by_slo.setdefault(rule["labels"]["slo"], []).append(rule["alert"])
            assert rule["expr"].count(" and ") == 1, f"{rule['alert']}: burn-rate alerts need a long and a short window"
    assert by_slo and all(len(alerts) == 2 for alerts in by_slo.values()), by_slo
    for alerts in by_slo.values():
        severities = {next(r for r in _alerts() if r["alert"] == a)["labels"]["severity"] for a in alerts}
        assert severities == {"critical", "warning"}


# --- the metrics -------------------------------------------------------------------------------------------------------------------

def test_the_metric_scan_finds_the_series_the_rules_rely_on():
    exported = _exported_metrics()
    for name in ("smo_http_requests_total", "smo_http_request_duration_seconds_bucket", "smo_outbox_rows", "smo_rapp_instances", "smo_refusals_total",
                 "smo_worker_task_runs_total", "smo_audit_writes_total", "smo_db_pool_capacity", "smo_outbound_calls_total"):
        assert name in exported, name
    assert exported["smo_outbox_rows"] == {"module", "status"}


def test_every_metric_and_label_a_rule_uses_is_exported_by_the_code_or_recorded_by_the_file():
    exported = _exported_metrics()
    recorded = {rule["record"] for rule in _recordings()}
    for rule in _rules():
        used = list(_selectors(rule["expr"]))
        assert used, f"{rule.get('alert') or rule['record']}: no series found in {rule['expr']!r} (is the scan wrong?)"
        for name, labels in used:
            where = rule.get("alert") or rule["record"]
            if name == "up" or name in OPERATOR_METRICS:
                continue
            if name.startswith("smo:"):
                assert name in recorded, f"{where}: {name} is not recorded by this file"
                continue
            assert name in exported, f"{where}: {name} is not a metric the code exports"
            unknown = labels - exported[name] - SCRAPE_LABELS
            assert not unknown, f"{where}: {name} has no label {sorted(unknown)} (it has {sorted(exported[name])})"


def test_the_operator_metrics_the_backup_alerts_use_are_the_ones_the_ci_job_checks_on_a_real_instance():
    used = {name for rule in _rules() for name, _ in _selectors(rule["expr"]) if name.startswith("cnpg_")}
    assert used == OPERATOR_METRICS
    job = (SMO_ROOT.parent / ".github" / "workflows" / "smo-dr.yml").read_text()
    for name in OPERATOR_METRICS:
        assert name in job, f"{name} is not checked against a real instance by the cnpg-wal-archive job"


def test_every_recording_rule_is_used_by_an_alert_or_another_rule():
    text = " ".join(rule["expr"] for rule in _rules())
    for rule in _recordings():
        assert rule["record"] in text or rule["record"].rsplit(":", 1)[0] in text, f"{rule['record']} is never used"


def test_every_module_job_in_the_down_alert_is_a_module_of_the_chart_and_every_service_module_is_listed():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    defaults = values["moduleDefaults"]
    services = {name for name, spec in values["modules"].items() if {**defaults, **spec}["kind"] == "service"}
    down = next(rule for rule in _alerts() if rule["alert"] == "SmoModuleDown")
    listed = set(re.search(r'job=~"([^"]+)"', down["expr"]).group(1).split("|"))
    assert listed == services


# --- runbooks ----------------------------------------------------------------------------------------------------------------------

def test_every_alert_links_to_a_runbook_page_that_exists_and_is_named_after_it():
    for rule in _alerts():
        url = rule["annotations"]["runbook_url"]
        assert url == f"{RUNBOOK_URL_PREFIX}{rule['alert']}.md", url
        assert (RUNBOOKS / url.removeprefix(RUNBOOK_URL_PREFIX)).is_file(), f"{url} does not resolve to a file"


def test_every_runbook_has_the_five_sections_and_belongs_to_an_alert():
    alerts = {rule["alert"] for rule in _alerts()}
    pages = {path.stem: path for path in RUNBOOKS.glob("*.md") if path.name != "README.md"}
    assert set(pages) == alerts, f"runbooks without an alert, or alerts without a runbook: {set(pages) ^ alerts}"
    for name, path in pages.items():
        text = path.read_text()
        assert text.startswith(f"# {name}"), name
        headings = re.findall(r"^## (.+)$", text, re.M)
        assert headings == list(SECTIONS), f"{name}: headings are {headings}"
        for section in SECTIONS:
            body = text.split(f"## {section}", 1)[1].split("\n## ", 1)[0].strip()
            assert len(body) > 40, f"{name}: section {section} is empty"
        assert "```" in text.split("## Diagnosis", 1)[1].split("\n## ", 1)[0], f"{name}: Diagnosis has no command"


def test_the_runbook_index_lists_every_page_and_its_links_resolve():
    index = (RUNBOOKS / "README.md").read_text()
    for rule in _alerts():
        assert f"({rule['alert']}.md)" in index, rule["alert"]
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", index):
        if not target.startswith("http"):
            assert (RUNBOOKS / target).resolve().exists(), target


# --- the SLO document --------------------------------------------------------------------------------------------------------------

def test_the_slo_document_names_every_slo_alert_and_says_for_which_deployment_the_targets_hold():
    text = SLOS.read_text()
    for slo in {rule["labels"]["slo"] for rule in _alerts() if "slo" in rule["labels"]}:
        assert f"`{slo}`" in text, slo
    for rule in _alerts():
        if "slo" in rule["labels"]:
            assert f"`{rule['alert']}`" in text, rule["alert"]
    assert "accepted as the reference targets" in text and "one-pod lab profile" in text      # decided October 2026: the status and its scope are stated
    targets = text.split("## Targets", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in targets.splitlines() if re.match(r"\| `[a-z-]+` \|", line)]
    assert len(rows) >= 3 and "Target" in targets.splitlines()[2], "the target table is there"


# --- the chart ---------------------------------------------------------------------------------------------------------------------

def test_the_chart_embeds_the_rules_file_behind_a_value_that_is_off_by_default():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["prometheusRule"]["enabled"] is False
    template = (CHART / "templates" / "prometheusrule.yaml").read_text()
    assert "if .Values.prometheusRule.enabled" in template and 'Files.Get "files/smo-alerts.rules.yaml"' in template
    assert "kind: PrometheusRule" in template


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_rendered_prometheusrule_carries_the_same_groups_and_is_absent_by_default():
    base = ["helm", "template", "smo", str(CHART), "--kube-version", "1.30.0"]
    off = subprocess.run(base, capture_output=True, text=True, check=True).stdout
    assert "PrometheusRule" not in off
    on = subprocess.run([*base, "--set", "prometheusRule.enabled=true"], capture_output=True, text=True, check=True).stdout
    doc = next(d for d in yaml.safe_load_all(on) if d and d["kind"] == "PrometheusRule")
    assert doc["spec"] == RULES


@pytest.mark.skipif(shutil.which("promtool") is None, reason="promtool is not installed")
def test_promtool_accepts_the_file():
    result = subprocess.run(["promtool", "check", "rules", str(RULES_FILE)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
