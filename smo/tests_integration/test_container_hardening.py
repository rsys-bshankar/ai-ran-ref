"""Containers run unprivileged (PR-SEC-13.1, 13.3): a non-root user, no capabilities, no privilege escalation."""

import re
from pathlib import Path

import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (SMO_ROOT / "Dockerfile").read_text()


def _compose() -> dict:
    text = (SMO_ROOT / "docker-compose.yml").read_text()
    return yaml.safe_load(text)["services"]


def _instructions(text: str) -> list[str]:
    """Dockerfile instructions with line continuations joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", text)
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_the_image_ends_as_a_numeric_non_root_user_before_it_starts_the_service():
    instructions = _instructions(DOCKERFILE)
    users = [i for i, line in enumerate(instructions) if line.startswith("USER ")]
    assert users, "the Dockerfile never drops root"
    last = instructions[users[-1]].split()[1]
    uid = last.split(":")[0]
    assert uid.isdigit() and int(uid) > 0, f"USER {last}: a numeric non-root uid lets the runtime check it"
    assert users[-1] < next(i for i, line in enumerate(instructions) if line.startswith("CMD ")), "CMD runs as root"


def test_the_two_directories_a_service_writes_exist_and_belong_to_that_user():
    run = " ".join(line for line in _instructions(DOCKERFILE) if line.startswith("RUN "))
    assert re.search(r"mkdir -p /data /srv/packages", run) and re.search(r"chown smo:smo /data /srv/packages", run)
    compose = (SMO_ROOT / "docker-compose.yml").read_text()
    assert "gui_bff_data:/data" in compose and "smo_packages:/srv/packages" in compose


def test_every_service_we_build_drops_every_capability_and_cannot_gain_privileges():
    services = _compose()
    built = {name: svc for name, svc in services.items() if "build" in svc}
    assert len(built) >= 25, "the compose file lost services"
    for name, svc in built.items():
        assert svc.get("cap_drop") == ["ALL"], f"{name} keeps Linux capabilities"
        assert "no-new-privileges:true" in svc.get("security_opt", []), f"{name} can gain privileges"


def test_no_service_asks_for_more_privilege_than_the_default():
    for name, svc in _compose().items():
        assert not svc.get("privileged"), name
        assert svc.get("network_mode") != "host" and svc.get("pid") != "host" and svc.get("ipc") != "host", name
        assert not any("docker.sock" in str(v) for v in svc.get("volumes", [])), name
        assert not svc.get("cap_add"), name


def test_postgres_is_left_with_its_defaults_because_its_entrypoint_drops_privileges_itself():
    postgres = _compose()["postgres"]
    assert "cap_drop" not in postgres
