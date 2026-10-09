"""A rApp manifest's runtime profile as Kubernetes container resources (PR-RAPP-2.1).

A manifest's `runtimeProfiles.<MODE>` is `{cpu, memory, gpu}`: CPU cores (a number), memory (a Kubernetes quantity such as `16Gi`), GPUs. The NFO
descriptor carries it twice: `workloadTemplate.resources` is the profile as written (unchanged, what AIMgF has always stored), and
`workloadTemplate.containerResources` is `container_resources(profile)`, the `resources:` block of a container spec a deployment manager can apply as it is:

    {cpu: 4, memory: 8Gi, gpu: 0}  ->  {requests: {cpu: "4", memory: 8Gi}, limits: {cpu: "4", memory: 8Gi}}

Requests equal limits on purpose: the profile says what the runtime needs, and a pod that asks for less than it may use is the one a busy node squeezes
(the `Guaranteed` QoS class). A fractional CPU is written in millicores (`0.5` -> `500m`). A zero or an absent value sets nothing for that resource. GPUs are not
mapped: the resource name is vendor specific (`nvidia.com/gpu`, ...) and where a GPU runs is NFO's decision through `requiredResourceTypeId`, so `gpu` stays in
`resources` only.
"""

import re

# the Kubernetes quantity grammar for the plain forms a manifest uses: digits with an optional fraction, then a binary or decimal suffix or an exponent
QUANTITY_PATTERN = r"^[0-9]+(\.[0-9]+)?([EPTGMk]i?|m|[eE][+-]?[0-9]+)?$"
_QUANTITY = re.compile(QUANTITY_PATTERN)


def is_quantity(value: object) -> bool:
    return isinstance(value, str) and _QUANTITY.fullmatch(value) is not None


def cpu_quantity(cores: float) -> str:
    """Cores as a quantity: whole numbers as they are, anything else in millicores (at least `1m`)."""
    if float(cores).is_integer():
        return str(int(cores))
    return f"{max(1, round(float(cores) * 1000))}m"


def container_resources(profile: object) -> dict:
    """The `resources:` block for a `{cpu, memory, gpu}` profile; `{}` when it sets neither a CPU nor a valid memory (nothing to request or limit)."""
    if not isinstance(profile, dict):
        return {}
    wanted: dict[str, str] = {}
    cpu = profile.get("cpu")
    if isinstance(cpu, (int, float)) and not isinstance(cpu, bool) and cpu > 0:
        wanted["cpu"] = cpu_quantity(cpu)
    memory = profile.get("memory")
    if isinstance(memory, str) and is_quantity(memory) and not _is_zero(memory):
        wanted["memory"] = memory
    return {"requests": dict(wanted), "limits": dict(wanted)} if wanted else {}


def _is_zero(quantity: str) -> bool:
    number = re.match(r"[0-9.]+", quantity)
    return number is None or float(number.group()) == 0.0
