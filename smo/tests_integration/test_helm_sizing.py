"""The chart's resource requests (PR-OPS-9): the defaults fit a small cluster, the sized profile is what docs/SIZING.md says it is. Plain YAML, no `helm` needed."""

import re
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parent.parent / "deploy" / "helm" / "smo"
DOCS = Path(__file__).resolve().parent.parent / "docs"


def _cpu_milli(text) -> int:
    text = str(text)
    return int(text[:-1]) if text.endswith("m") else int(float(text) * 1000)


def _mem_mib(text) -> int:
    match = re.fullmatch(r"(\d+)(Mi|Gi)", str(text))
    assert match, text
    return int(match.group(1)) * (1024 if match.group(2) == "Gi" else 1)


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _totals(values: dict) -> tuple[int, int]:
    """CPU (milli) and memory (MiB) requested by one replica set of every enabled module and the bundled Postgres."""
    defaults = values["moduleDefaults"]
    cpu = mem = 0
    for module in values["modules"].values():
        merged = _merge(defaults, module)
        if not merged.get("enabled", True):
            continue
        request = merged["resources"]["requests"]
        cpu += _cpu_milli(request["cpu"]) * merged["replicas"]
        mem += _mem_mib(request["memory"]) * merged["replicas"]
    request = values["postgres"]["resources"]["requests"]
    return cpu + _cpu_milli(request["cpu"]), mem + _mem_mib(request["memory"])


def _values():
    return yaml.safe_load((CHART / "values.yaml").read_text())


def _sized():
    return yaml.safe_load((CHART / "values-sized.yaml").read_text())


def test_the_defaults_fit_a_small_cluster():
    """A lab or trial install, and the kind job, run on a node with two cores; a rolling upgrade needs room beside the old pods. Raising a default
    request past this is what `values-sized.yaml` is for."""
    cpu, _ = _totals(_values())
    assert cpu <= 1500, f"the default CPU requests add up to {cpu}m: put the increase in values-sized.yaml"


def test_the_sized_profile_only_names_what_the_chart_has_and_keeps_limits_above_requests():
    """The sized values file names only modules the chart has, every memory limit is at least its request and no CPU limit is set (docs/SIZING.md)."""
    values, sized = _values(), _sized()
    assert set(sized["modules"]) <= set(values["modules"])
    merged = _merge(values, sized)
    resources = {"postgres": merged["postgres"]["resources"]}
    resources |= {name: _merge(merged["moduleDefaults"], merged["modules"][name])["resources"] for name in sized["modules"]}
    for name, one in resources.items():
        assert _mem_mib(one["limits"]["memory"]) >= _mem_mib(one["requests"]["memory"]), name
        assert "cpu" not in one["limits"], f"{name}: no CPU limits (docs/SIZING.md)"


def test_the_totals_in_the_sizing_document_are_the_totals_of_the_sized_profile():
    """The CPU and memory totals docs/SIZING.md quotes are the totals of the sized profile."""
    cpu, mem = _totals(_merge(_values(), _sized()))
    text = (DOCS / "SIZING.md").read_text()
    assert f"about {cpu / 1000:.1f} cores and {mem / 1024:.1f} GiB" in text, (cpu, mem)
