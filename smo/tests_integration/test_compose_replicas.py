"""PR-HA-1.2: the compose override that runs two replicas names real services, and the same ones as the Helm values do."""

from pathlib import Path

import yaml

SMO = Path(__file__).resolve().parent.parent


class _Loader(yaml.SafeLoader):
    """Compose's `!reset` tag (empty the inherited list) reads as the empty list here."""


_Loader.add_constructor("!reset", lambda loader, node: [])
REPLICAS = yaml.load((SMO / "docker-compose.replicas.yml").read_text(), Loader=_Loader)["services"]
COMPOSE = yaml.safe_load((SMO / "docker-compose.yml").read_text())["services"]
HELM = yaml.safe_load((SMO / "deploy/helm/smo/ci/ha-values.yaml").read_text())["modules"]


def test_every_replicated_service_exists_in_compose_and_runs_two():
    assert set(REPLICAS) <= set(COMPOSE)
    assert all(svc["deploy"]["replicas"] == 2 for svc in REPLICAS.values())


def test_compose_and_helm_replicate_the_same_modules():
    assert set(REPLICAS) == set(HELM)


def test_a_replicated_service_does_not_publish_a_host_port_two_containers_cannot_share():
    # the override resets them; anything left in the base file for a replicated service would make the second container fail to start
    for name, svc in REPLICAS.items():
        if COMPOSE[name].get("ports"):
            assert svc.get("ports") == [], f"{name} publishes a host port and the override does not drop it"
