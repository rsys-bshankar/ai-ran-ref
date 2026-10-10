"""PR-RAPP-2.1: a manifest runtime profile as the container resources of a Kubernetes spec.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_runtime_resources.py -q
"""

import pytest

from smo_shared.runtime_resources import container_resources, cpu_quantity, is_quantity


def test_cpu_and_memory_become_equal_requests_and_limits():
    """A runtime profile's cpu and memory become identical Kubernetes requests and limits; a gpu entry is not mapped."""
    assert container_resources({"cpu": 4, "memory": "8Gi", "gpu": 0}) == {"requests": {"cpu": "4", "memory": "8Gi"}, "limits": {"cpu": "4", "memory": "8Gi"}}


def test_requests_and_limits_are_separate_objects_so_a_caller_can_change_one():
    """requests and limits are separate dicts, so changing one does not change the other."""
    resources = container_resources({"cpu": 1, "memory": "1Gi"})
    resources["limits"]["memory"] = "2Gi"
    assert resources["requests"]["memory"] == "1Gi"


# Table: (cores, quantity). Whole cores are written as an integer, fractions as millicores (rounded up to at least 1m).
@pytest.mark.parametrize("cores, expected", [(8, "8"), (2.0, "2"), (0.5, "500m"), (1.5, "1500m"), (0.0004, "1m"), (0.25, "250m")])
def test_cpu_is_whole_cores_or_millicores(cores, expected):
    assert cpu_quantity(cores) == expected


# Table: memory strings Kubernetes accepts (binary and decimal suffixes, plain numbers, exponents, milli).
@pytest.mark.parametrize("value", ["16Gi", "512Mi", "4G", "1.5Gi", "100", "129e6", "129E3", "1k", "500m", "2Ei"])
def test_the_quantities_a_manifest_uses_are_recognised(value):
    assert is_quantity(value)


# Table: strings and non-strings that are not valid quantities (empty, spaces, wrong suffix or case, negative, numbers, booleans, None).
@pytest.mark.parametrize("value", ["", "16 GB", "Gi", "-1Gi", "1gi", "1GiB", "1.Gi", None, 4096, True, "one"])
def test_anything_else_is_not_a_quantity(value):
    assert not is_quantity(value)


def test_a_missing_or_zero_value_sets_nothing_for_that_resource():
    """A missing or zero cpu or memory is left out entirely, and a profile with nothing usable gives an empty block."""
    assert container_resources({"cpu": 2}) == {"requests": {"cpu": "2"}, "limits": {"cpu": "2"}}
    assert container_resources({"memory": "4Gi", "cpu": 0}) == {"requests": {"memory": "4Gi"}, "limits": {"memory": "4Gi"}}
    assert container_resources({"memory": "0Gi", "cpu": 0, "gpu": 2}) == {}


# Table: profiles that are missing, the wrong type, have only a gpu, a boolean or negative cpu, a bad memory, or a cpu given as text; each gives an
# empty resources block.
@pytest.mark.parametrize("profile", [None, {}, "x", [], {"gpu": 1}, {"cpu": True}, {"cpu": -1}, {"memory": "lots"}, {"cpu": "4"}])
def test_nothing_usable_gives_an_empty_block_never_an_invalid_one(profile):
    assert container_resources(profile) == {}


def test_an_invalid_memory_is_dropped_but_a_valid_cpu_is_kept():
    """An invalid memory quantity is dropped without losing a valid cpu."""
    assert container_resources({"cpu": 2, "memory": "lots"}) == {"requests": {"cpu": "2"}, "limits": {"cpu": "2"}}


def test_gpus_are_not_mapped():
    """A gpu count in the profile does not produce an nvidia.com/gpu resource."""
    assert "nvidia.com/gpu" not in str(container_resources({"cpu": 1, "memory": "1Gi", "gpu": 2}))
