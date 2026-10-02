"""The compose Postgres logs slow statements (PR-DB-4.1)."""

import os
import re
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, text

SMO_ROOT = Path(__file__).resolve().parent.parent


def _command() -> list[str]:
    compose = (SMO_ROOT / "docker-compose.yml").read_text()
    return yaml.safe_load(compose)["services"]["postgres"]["command"]


def _setting(milliseconds: str) -> str:
    """The compose option with POSTGRES_SLOW_QUERY_MS set to `milliseconds`, as `name=value`."""
    (option,) = [part for part in _command() if part.startswith("log_min_duration_statement=")]
    return option.replace("${POSTGRES_SLOW_QUERY_MS:-500}", milliseconds)


def test_compose_starts_postgres_with_a_slow_statement_threshold_that_defaults_to_500_ms():
    command = _command()
    assert command[:2] == ["postgres", "-c"]               # the image's entrypoint still initialises the database
    assert "log_min_duration_statement=${POSTGRES_SLOW_QUERY_MS:-500}" in command
    assert re.search(r"^# POSTGRES_SLOW_QUERY_MS=", (SMO_ROOT / ".env.example").read_text(), re.M)


@pytest.mark.skipif(not os.environ.get("SMO_TEST_POSTGRES_URL"), reason="SMO_TEST_POSTGRES_URL not set")
@pytest.mark.parametrize("milliseconds,shown", [("500", "500ms"), ("0", "0"), ("-1", "-1")])
def test_a_real_postgres_accepts_the_setting_with_each_documented_value(milliseconds, shown):
    engine = create_engine(os.environ["SMO_TEST_POSTGRES_URL"], future=True,
                           connect_args={"options": f"-c {_setting(milliseconds)}"})
    with engine.connect() as connection:
        assert connection.execute(text("SHOW log_min_duration_statement")).scalar() == shown
    engine.dispose()
