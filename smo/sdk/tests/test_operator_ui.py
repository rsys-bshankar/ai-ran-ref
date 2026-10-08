"""smo_sdk.operator_ui: the builders, the check, the manifest writer and the minimal example (GUI-8.8)."""

import importlib.util
import io
import zipfile
from pathlib import Path

import pytest
import yaml

from smo_sdk import operator_ui as ui

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "hello_operator_ui.py"
ADR_EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "operator-ui.energy-saving.example.yaml"


def _example_module():
    spec = importlib.util.spec_from_file_location("hello_operator_ui", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builders_drop_unset_fields():
    assert ui.column("a", "A") == {"path": "a", "label": "A"}
    assert ui.source("/x") == {"path": "/x"}
    assert ui.source("/x", query={"n": 1}, refresh_seconds=10) == {"path": "/x", "query": {"n": 1}, "refreshSeconds": 10}
    assert ui.input_field("n", "N", "integer", min=1, max=3) == {"name": "n", "label": "N", "type": "integer", "min": 1, "max": 3}
    assert ui.tile("T", kpi="k") == {"label": "T", "kpi": "k"}


def test_a_string_source_is_a_route():
    panel = ui.key_values("kv", "KV", "/instances/{instanceId}", [ui.item("A", "a")])
    assert panel["source"] == {"path": "/instances/{instanceId}"}


def test_declaration_validates_and_marks_read_only():
    out = ui.declaration(ui.key_values("kv", "KV", "/k", [ui.item("A", "a")]), read_only=True)
    assert out == {"version": 1, "readOnly": True, "panels": out["panels"]}
    assert ui.declaration(ui.key_values("kv", "KV", "/k", [ui.item("A", "a")]))["readOnly"] is False


def test_a_mistake_is_raised_where_it_is_written_not_at_onboarding():
    with pytest.raises(ui.OperatorUiInvalid, match=r"operatorUi.panels\[0\].source.path: must not contain '..'"):
        ui.declaration(ui.key_values("kv", "KV", "/a/../b", [ui.item("A", "a")]))
    with pytest.raises(ui.OperatorUiInvalid, match="an action must change something"):
        ui.declaration(ui.actions("a", "A", [ui.action("go", "Go", "GET", "/g", success="ok")]))
    with pytest.raises(ui.OperatorUiInvalid, match="needs 'y'"):
        ui.declaration(ui.table("t", "T", "/t", row_key="id", columns=[ui.column("trend", "Trend", "sparkline")]))


def test_a_declaration_with_a_sparkline_row_action_and_a_kpi_panel_builds():
    out = ui.declaration(
        ui.table("cells", "Cells", ui.source("/instances/{instanceId}/cells"), rows="items", row_key="cellId",
                 columns=[ui.column("cellId", "Cell"), ui.column("trend", "Trend", "sparkline", y="v")],
                 row_actions=[ui.action("clear", "Clear", "DELETE", "/instances/{instanceId}/cells/{row.cellId}/override", success="Cleared",
                                        when={"path": "overrideBy", "exists": True})]),
        ui.kpis("k", "KPIs", [ui.tile("Rate", kpi="success_rate", format="percent")]),
        ui.chart("c", "Load", "/instances/{instanceId}/load", points="items", x="t", y="v", type="bar"))
    assert [(m, p) for m, p in ui.declared_routes(out)] == [
        ("GET", "/instances/{instanceId}/cells"), ("DELETE", "/instances/{instanceId}/cells/{row.cellId}/override"), ("GET", "/instances/{instanceId}/load")]


def test_yaml_output_round_trips_in_block_style():
    out = ui.declaration(ui.table("t", "T", "/instances/{instanceId}/t", row_key="id", columns=[ui.column("id", "Id")]))
    text = ui.to_yaml(out)
    assert text.startswith("operatorUi:\n  version: 1\n") and "{" not in text.replace("{instanceId}", "")
    assert yaml.safe_load(text)["operatorUi"] == out


def test_add_to_manifest_appends_and_keeps_comments(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("# my rApp\nrappManifest:\n  manifestVersion: \"1.0\"\nname: X   # inline\n")
    ui.add_to_manifest(manifest, ui.declaration(ui.key_values("kv", "KV", "/k", [ui.item("A", "a")])))
    text = manifest.read_text()
    assert text.startswith("# my rApp\nrappManifest:") and "name: X   # inline\n\noperatorUi:\n  version: 1" in text
    parsed = yaml.safe_load(text)
    assert parsed["operatorUi"]["panels"][0]["id"] == "kv" and parsed["name"] == "X"
    ui.validate(parsed["operatorUi"])


def test_add_to_manifest_creates_the_file_and_refuses_a_second_declaration(tmp_path):
    page = ui.declaration(ui.key_values("kv", "KV", "/k", [ui.item("A", "a")]))
    manifest = tmp_path / "manifest.yaml"
    ui.add_to_manifest(manifest, page)
    with pytest.raises(ValueError, match="already has an operatorUi"):
        ui.add_to_manifest(manifest, page)
    nested = tmp_path / "nested.yaml"
    nested.write_text("rappManifest:\n  operatorUi: {}\n")
    with pytest.raises(ValueError, match="already has an operatorUi"):
        ui.add_to_manifest(nested, page)
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("- 1\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        ui.add_to_manifest(scalar, page)


def test_add_to_manifest_checks_before_writing(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("name: X\n")
    with pytest.raises(ui.OperatorUiInvalid):
        ui.add_to_manifest(manifest, {"version": 1, "panels": [{"id": "x", "title": "X", "kind": "map"}]})
    assert manifest.read_text() == "name: X\n"


def test_the_minimal_example_builds_a_package_whose_declaration_passes_onboardings_check():
    hello = _example_module()
    with zipfile.ZipFile(io.BytesIO(hello.build_bytes())) as z:
        assert sorted(z.namelist()) == ["Definitions/asd.yaml", "TOSCA-Metadata/TOSCA.meta", "manifest.yaml"]
        manifest = yaml.safe_load(z.read("manifest.yaml"))
    declared = ui.validate(manifest["operatorUi"])
    assert [p["kind"] for p in declared["panels"]] == ["keyValues", "kpis", "table", "actions"]
    assert ("POST", "/instances/{instanceId}/run") in ui.declared_routes(declared)
    assert hello.build_bytes() == hello.build_bytes()          # byte-identical rebuilds


def test_the_adr_example_can_be_loaded_through_the_helper():
    assert ui.validate(yaml.safe_load(ADR_EXAMPLE.read_text())["operatorUi"])["panels"][2]["id"] == "cells"
