"""No default database URL (PR-DB-1): the process refuses to start without SMO_DATABASE_URL.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_db_url.py -q
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from smo_shared.db import MissingDatabaseUrl, TEST_DATABASE_URL, resolve_database_url

SHARED = Path(__file__).resolve().parent.parent


def test_the_configured_url_is_used_as_given():
    """A configured SMO_DATABASE_URL is returned unchanged."""
    url = "postgresql+psycopg://u:p@db.example:5432/smo"
    assert resolve_database_url({"SMO_DATABASE_URL": url}, allow_sqlite_fallback=False) == url


# Table: unset, empty and whitespace-only URLs. Each must raise MissingDatabaseUrl with a message that names the variable, says there is no default
# and points to init_secrets.sh, and must not contain a known password.
@pytest.mark.parametrize("environ", [{}, {"SMO_DATABASE_URL": ""}, {"SMO_DATABASE_URL": "   "}])
def test_an_unset_or_blank_url_outside_tests_is_refused_with_a_message_that_says_what_to_do(environ):
    with pytest.raises(MissingDatabaseUrl) as error:
        resolve_database_url(environ, allow_sqlite_fallback=False)
    message = str(error.value)
    assert "SMO_DATABASE_URL" in message and "init_secrets.sh" in message and "no default" in message
    assert "smo:smo" not in message


def test_with_the_explicit_opt_in_an_unset_url_is_an_in_memory_database_never_a_server():
    """With the fallback switched on (argument or `SMO_ALLOW_SQLITE_FALLBACK`) an unset URL means in-memory SQLite, never a default server address."""
    assert resolve_database_url({}, allow_sqlite_fallback=True) == TEST_DATABASE_URL == "sqlite://"
    assert resolve_database_url({"SMO_ALLOW_SQLITE_FALLBACK": "1"}) == "sqlite://"
    assert resolve_database_url({"SMO_ALLOW_SQLITE_FALLBACK": "true"}) == "sqlite://"


# Table: values of SMO_ALLOW_SQLITE_FALLBACK that do not switch the fallback on; each must still be refused.
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_a_false_or_unknown_opt_in_value_does_not_enable_the_fallback(value):
    """Only 1/true/yes/on enable the SQLite fallback; a typo must not put a production process on an in-memory database."""
    with pytest.raises(MissingDatabaseUrl):
        resolve_database_url({"SMO_ALLOW_SQLITE_FALLBACK": value})


def test_pytest_being_imported_is_not_an_opt_in():
    """Regression: the fallback used to switch on whenever `pytest` was in sys.modules, so a production process that imported pytest silently used SQLite.
    A fresh interpreter that imports pytest but has no opt-in must still refuse to start."""
    result = _import_db({}, "import pytest; import smo_shared.db as d; print(d.DATABASE_URL)")
    assert result.returncode != 0 and "MissingDatabaseUrl" in result.stderr


def test_a_real_process_with_the_opt_in_and_no_url_uses_sqlite():
    """The opt-in works at process start too (this is what every test suite's conftest relies on)."""
    result = _import_db({"SMO_ALLOW_SQLITE_FALLBACK": "1"})
    assert result.returncode == 0 and result.stdout.strip() == "sqlite://"


def _import_db(environ_extra, code="import smo_shared.db as d; print(d.DATABASE_URL)"):
    """Helper: imports smo_shared.db in a fresh interpreter with SMO_DATABASE_URL and SMO_ALLOW_SQLITE_FALLBACK removed from the environment (then `environ_extra` applied), so the
    process-start behaviour is tested for real.
    """
    environ = {k: v for k, v in os.environ.items() if k not in ("SMO_DATABASE_URL", "SMO_ALLOW_SQLITE_FALLBACK")}
    environ.update(environ_extra)
    environ["PYTHONPATH"] = str(SHARED)
    return subprocess.run([sys.executable, "-c", code],
                          env=environ, capture_output=True, text=True, timeout=60)


def test_a_real_process_without_the_variable_exits_non_zero_before_serving():
    """A real process importing the database module without the URL exits non-zero with the MissingDatabaseUrl message, so a misconfigured service
    never starts.
    """
    result = _import_db({})
    assert result.returncode != 0
    assert "MissingDatabaseUrl" in result.stderr and "SMO_DATABASE_URL is not set" in result.stderr


def test_a_real_process_with_the_variable_starts():
    """With the variable set, the same import succeeds and uses that URL."""
    result = _import_db({"SMO_DATABASE_URL": "sqlite://"})
    assert result.returncode == 0 and result.stdout.strip() == "sqlite://"
