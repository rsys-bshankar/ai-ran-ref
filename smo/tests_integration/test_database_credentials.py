"""No default database credentials anywhere in the repo's service code or deployment files (PR-DB-1)."""

import re
from pathlib import Path

import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
LITERAL_PASSWORD = re.compile(r"smo:smo@")


def test_no_service_or_script_source_carries_the_old_default_url():
    offenders = []
    for path in list(SMO_ROOT.glob("*/app/**/*.py")) + list(SMO_ROOT.glob("samples/*/app/**/*.py")) + \
            list(SMO_ROOT.glob("shared/smo_shared/**/*.py")) + list(SMO_ROOT.glob("scripts/*.py")):
        if LITERAL_PASSWORD.search(path.read_text()):
            offenders.append(str(path.relative_to(SMO_ROOT)))
    assert offenders == [], f"a default database URL with a password in source: {offenders}"


def test_compose_takes_the_database_password_from_the_environment_and_requires_it():
    text = (SMO_ROOT / "docker-compose.yml").read_text()
    assert not LITERAL_PASSWORD.search(text)
    services = yaml.safe_load(text.replace("${POSTGRES_PASSWORD:?", "${POSTGRES_PASSWORD_REQUIRED:?"))["services"]
    assert "POSTGRES_PASSWORD_REQUIRED:?" in services["postgres"]["environment"]["POSTGRES_PASSWORD"]
    urls = [s["environment"]["SMO_DATABASE_URL"] for s in services.values()
            if isinstance(s.get("environment"), dict) and "SMO_DATABASE_URL" in s["environment"]]
    assert len(urls) >= 15 and all("POSTGRES_PASSWORD_REQUIRED:?" in url for url in urls)


def test_the_example_env_names_the_password_and_the_real_env_is_ignored():
    example = (SMO_ROOT / ".env.example").read_text()
    assert re.search(r"^POSTGRES_PASSWORD=\S+", example, re.M)
    assert not LITERAL_PASSWORD.search(example) and "POSTGRES_PASSWORD=smo" not in example
    assert ".env" in (SMO_ROOT / ".gitignore").read_text().splitlines()
