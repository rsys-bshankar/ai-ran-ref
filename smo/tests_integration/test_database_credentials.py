"""No default database credentials anywhere in the repo's service code or deployment files (PR-DB-1)."""

import re
from pathlib import Path

import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
LITERAL_PASSWORD = re.compile(r"smo:smo@")


def test_no_service_or_script_source_carries_the_old_default_url():
    """No service, shared or script source carries a default database URL with a password in it."""
    offenders = []
    for path in list(SMO_ROOT.glob("*/app/**/*.py")) + list(SMO_ROOT.glob("samples/*/app/**/*.py")) + \
            list(SMO_ROOT.glob("shared/smo_shared/**/*.py")) + list(SMO_ROOT.glob("scripts/*.py")):
        if LITERAL_PASSWORD.search(path.read_text()):
            offenders.append(str(path.relative_to(SMO_ROOT)))
    assert offenders == [], f"a default database URL with a password in source: {offenders}"


def _compose() -> dict:
    return yaml.safe_load((SMO_ROOT / "docker-compose.yml").read_text())


def test_the_database_password_is_a_compose_secret_file_never_a_literal_or_an_environment_value():
    """In docker-compose.yml the database password is a secret file for Postgres and is never a literal or an environment value, and every
    service's database URL names the Postgres host and no password.
    """
    compose = _compose()
    text = (SMO_ROOT / "docker-compose.yml").read_text()
    assert not LITERAL_PASSWORD.search(text) and "POSTGRES_PASSWORD:" not in text
    assert compose["secrets"]["db_password"]["file"] == "./secrets/db_password"
    postgres = compose["services"]["postgres"]
    assert postgres["environment"]["POSTGRES_PASSWORD_FILE"] == "/run/secrets/db_password"
    assert "db_password" in postgres["secrets"]
    users = {name: svc for name, svc in compose["services"].items()
             if isinstance(svc.get("environment"), dict) and "SMO_DATABASE_URL" in svc["environment"]}
    assert len(users) >= 15
    for name, svc in users.items():
        env = svc["environment"]
        assert ("@postgres" in env["SMO_DATABASE_URL"] or "@${SMO_DB_HOST:-postgres}:" in env["SMO_DATABASE_URL"]) and ":" not in env["SMO_DATABASE_URL"].split("//", 1)[1].split("@")[0], \
            f"{name}: the URL carries a password"
        user = env["SMO_DATABASE_URL"].split("//", 1)[1].split("@")[0]
        # the owner (`smo`) uses db_password; a module with a role of its own (PR-DB-2.6) uses the file of that role, and does not get the owner's
        if user == "smo":
            secret = "db_password"
        else:   # smo_energy_saving_rapp -> the secret db_password_energy-saving-rapp (a role name has underscores, a module name hyphens)
            matches = [x for x in svc["secrets"] if x.startswith("db_password_") and x.removeprefix("db_password_").replace("-", "_") == user.removeprefix("smo_")]
            assert len(matches) == 1, f"{name}: user {user} has no matching role secret in {svc['secrets']}"
            secret = matches[0]
        assert env["SMO_DATABASE_PASSWORD_FILE"] == f"/run/secrets/{secret}", name
        assert secret in svc["secrets"], f"{name} cannot read the secret it is told to"
        if secret != "db_password":
            assert "db_password" not in svc["secrets"], f"{name} has a role of its own and must not also hold the owner's password"


def test_the_secret_directory_and_the_real_env_file_are_git_ignored_and_the_example_holds_no_password():
    """The `secrets/` directory and the real `.env` are git-ignored and `.env.example` holds no password."""
    example = (SMO_ROOT / ".env.example").read_text()
    assert "POSTGRES_PASSWORD=" not in example and not LITERAL_PASSWORD.search(example)
    ignored = (SMO_ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored and "secrets/" in ignored


def test_init_secrets_creates_the_file_once_and_never_overwrites_it(tmp_path):
    """`init_secrets.sh` creates a random 48-hex-character password file once (in an owner-only directory, without printing it) and a second run
    keeps it.
    """
    import shutil
    import stat
    import subprocess
    root = tmp_path / "smo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SMO_ROOT / "scripts" / "init_secrets.sh", root / "scripts" / "init_secrets.sh")
    script = str(root / "scripts" / "init_secrets.sh")
    first = subprocess.run([script], capture_output=True, text=True, check=True)
    secret = root / "secrets" / "db_password"
    value = secret.read_text()
    assert first.stdout.startswith("created:") and re.fullmatch(r"[0-9a-f]{48}", value) and value not in first.stdout
    assert stat.S_IMODE(secret.parent.stat().st_mode) == 0o700 and stat.S_IMODE(secret.stat().st_mode) == 0o644
    again = subprocess.run([script], capture_output=True, text=True, check=True)
    assert again.stdout.startswith("kept:") and secret.read_text() == value


def test_every_fuzz_target_that_imports_an_app_sets_a_database_url_first():
    # a fuzz target runs outside pytest, where an unset URL is refused; it found this the hard way in CI
    """A fuzz target that imports an app sets `SMO_DATABASE_URL` first, because a fuzz target runs outside pytest where an unset URL is refused."""
    for path in (SMO_ROOT / "fuzz").glob("fuzz_*.py"):
        text = path.read_text()
        if re.search(r"^\s*from app\.|^\s*import app\b", text, re.M):
            assert "SMO_DATABASE_URL" in text, f"{path.name} imports an app without setting SMO_DATABASE_URL"
