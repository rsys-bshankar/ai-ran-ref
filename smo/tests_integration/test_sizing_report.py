"""scripts/sizing_report.py (PR-OPS-9.2): `docker stats` samples in, a sizing table out."""

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("sizing_report", Path(__file__).resolve().parent.parent / "scripts" / "sizing_report.py")
sizing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sizing)


def line(name, cpu, mem):
    return json.dumps({"Name": name, "CPUPerc": f"{cpu}%", "MemUsage": f"{mem} / 7.7GiB"})


@pytest.mark.parametrize("text,expected", [("1MiB", 1024**2), ("1.5GiB", int(1.5 * 1024**3)), ("512KiB", 512 * 1024), ("2MB", 2_000_000), ("7B", 7)])
def test_sizes_are_read(text, expected):
    assert sizing.to_bytes(text) == expected


def test_a_size_without_a_known_unit_is_refused():
    with pytest.raises(ValueError):
        sizing.to_bytes("12 parsecs")
    with pytest.raises(ValueError):
        sizing.to_bytes("lots")


@pytest.mark.parametrize("container,service", [("smo-r1-termination-1", "r1-termination"), ("smo_sme_2", "sme"), ("smo-gui-bff-1", "gui-bff")])
def test_the_compose_project_prefix_and_replica_suffix_come_off(container, service):
    assert sizing.service_name(container) == service


def test_the_peak_memory_and_the_p95_cpu_set_the_suggestion():
    rows = sizing.summarize([line("smo-sme-1", 10, "100MiB"), line("smo-sme-1", 20, "200MiB"), line("smo-sme-2", 90, "150MiB"), "", line("smo-dme-1", 1, "50MiB")], 2.0, 1.25)
    sme = next(r for r in rows if r["service"] == "sme")
    assert (sme["samples"], sme["memPeakMiB"]) == (3, 200.0)
    assert sme["suggest"] == {"memoryRequestMiB": 256, "memoryLimitMiB": 448, "cpuRequestMilli": 900}     # 200*1.25=250 -> 256; 400 -> 448; 90 % of a core = 900 m
    assert [r["service"] for r in rows] == ["dme", "sme"]


def test_a_container_that_is_not_the_stack_is_left_out():
    rows = sizing.summarize([line("friendly_dijkstra", 99, "167MiB"), line("smo-postgres-1", 20, "150MiB")], 2.0, 1.25)
    assert [r["service"] for r in rows] == ["postgres"]


def test_the_report_files_are_written_and_an_empty_input_fails(tmp_path, capsys):
    stats = tmp_path / "stats.jsonl"
    stats.write_text(line("smo-sme-1", 5, "64MiB") + "\n")
    assert sizing.main([str(stats), "--out", str(tmp_path / "out")]) == 0
    assert json.loads((tmp_path / "out" / "sizing.json").read_text())[0]["service"] == "sme"
    assert "| sme |" in (tmp_path / "out" / "sizing.md").read_text()
    stats.write_text("\n")
    assert sizing.main([str(stats), "--out", str(tmp_path / "out2")]) == 1
