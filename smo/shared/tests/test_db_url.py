"""No default database URL (PR-DB-1): the process refuses to start without SMO_DATABASE_URL."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from smo_shared.db import MissingDatabaseUrl, TEST_DATABASE_URL, resolve_database_url

SHARED = Path(__file__).resolve().parent.parent


def test_the_configured_url_is_used_as_given():
    url = "postgresql+psycopg://u:p@db.example:5432/smo"
    assert resolve_database_url({"SMO_DATABASE_URL": url}, under_pytest=False) == url


@pytest.mark.parametrize("environ", [{}, {"SMO_DATABASE_URL": ""}, {"SMO_DATABASE_URL": "   "}])
def test_an_unset_or_blank_url_outside_tests_is_refused_with_a_message_that_says_what_to_do(environ):
    with pytest.raises(MissingDatabaseUrl) as error:
        resolve_database_url(environ, under_pytest=False)
    message = str(error.value)
    assert "SMO_DATABASE_URL" in message and ".env.example" in message and "no default" in message
    assert "smo:smo" not in message


def test_under_pytest_an_unset_url_is_an_in_memory_database_never_a_server():
    assert resolve_database_url({}, under_pytest=True) == TEST_DATABASE_URL == "sqlite://"


def _import_db(environ_extra):
    environ = {k: v for k, v in os.environ.items() if k != "SMO_DATABASE_URL"}
    environ.update(environ_extra)
    environ["PYTHONPATH"] = str(SHARED)
    return subprocess.run([sys.executable, "-c", "import smo_shared.db as d; print(d.DATABASE_URL)"],
                          env=environ, capture_output=True, text=True, timeout=60)


def test_a_real_process_without_the_variable_exits_non_zero_before_serving():
    result = _import_db({})
    assert result.returncode != 0
    assert "MissingDatabaseUrl" in result.stderr and "SMO_DATABASE_URL is not set" in result.stderr


def test_a_real_process_with_the_variable_starts():
    result = _import_db({"SMO_DATABASE_URL": "sqlite://"})
    assert result.returncode == 0 and result.stdout.strip() == "sqlite://"
