"""smo_shared.secretfile — the `*_FILE` convention (PR-SEC-4.2) and its use for the database password.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_secretfile.py -q
"""

import pytest

from smo_shared.db import resolve_database_url
from smo_shared.secretfile import SecretConflict, SecretFileError, read_secret


def test_the_value_comes_from_the_variable_or_from_the_file(tmp_path):
    """A secret is read from the variable, or from the file named by NAME_FILE with the trailing newline removed."""
    assert read_secret("S", {"S": "plain"}) == "plain"
    path = tmp_path / "s"
    path.write_text("from-a-file\n")
    assert read_secret("S", {"S_FILE": str(path)}) == "from-a-file"       # the trailing newline is not part of it


def test_neither_set_is_none_and_an_empty_value_counts_as_not_set(tmp_path):
    """No variable, an empty variable and an empty file all mean None."""
    assert read_secret("S", {}) is None
    assert read_secret("S", {"S": ""}) is None
    empty = tmp_path / "empty"
    empty.write_text("")
    assert read_secret("S", {"S_FILE": str(empty)}) is None


def test_only_one_trailing_newline_is_removed_and_nothing_else_is_trimmed(tmp_path):
    """Only one trailing newline is removed; spaces and further newlines are part of the secret."""
    path = tmp_path / "s"
    path.write_text("  spaced secret \n\n")
    assert read_secret("S", {"S_FILE": str(path)}) == "  spaced secret \n"


def test_both_set_is_an_error_not_a_guess(tmp_path):
    """Setting both NAME and NAME_FILE raises SecretConflict instead of choosing one."""
    path = tmp_path / "s"
    path.write_text("x")
    with pytest.raises(SecretConflict, match="both S and S_FILE"):
        read_secret("S", {"S": "y", "S_FILE": str(path)})


def test_a_missing_file_names_the_variable_and_path_but_never_a_value(tmp_path):
    """A missing secret file raises SecretFileError naming the variable and path."""
    missing = tmp_path / "nope"
    with pytest.raises(SecretFileError) as error:
        read_secret("S", {"S_FILE": str(missing)})
    assert f"S_FILE={missing}" in str(error.value)


def test_it_reads_the_real_environment_by_default(monkeypatch, tmp_path):
    """Without an explicit mapping read_secret uses os.environ."""
    path = tmp_path / "s"
    path.write_text("live")
    monkeypatch.setenv("SMO_TEST_SECRET_FILE", str(path))
    assert read_secret("SMO_TEST_SECRET") == "live"


# ---------------------------------------------------------------- the database URL

def test_the_password_is_put_into_a_url_that_has_none(tmp_path):
    """SMO_DATABASE_PASSWORD_FILE adds the password to the URL, percent-encoded so any character works."""
    path = tmp_path / "db_password"
    path.write_text("p@ss/word:1\n")
    url = resolve_database_url({"SMO_DATABASE_URL": "postgresql+psycopg://smo@postgres:5432/smo",
                                "SMO_DATABASE_PASSWORD_FILE": str(path)}, under_pytest=False)
    assert url == "postgresql+psycopg://smo:p%40ss%2Fword%3A1@postgres:5432/smo"   # percent-encoded, so any character works


def test_a_password_in_a_file_replaces_one_already_in_the_url(tmp_path):
    """A password from the file replaces one already in the URL."""
    path = tmp_path / "db_password"
    path.write_text("new")
    url = resolve_database_url({"SMO_DATABASE_URL": "postgresql+psycopg://smo:old@db/smo",
                                "SMO_DATABASE_PASSWORD_FILE": str(path)}, under_pytest=False)
    assert url == "postgresql+psycopg://smo:new@db/smo"


def test_the_whole_url_may_come_from_a_file(tmp_path):
    """SMO_DATABASE_URL_FILE supplies the whole URL."""
    path = tmp_path / "url"
    path.write_text("postgresql+psycopg://u:p@h/d\n")
    assert resolve_database_url({"SMO_DATABASE_URL_FILE": str(path)}, under_pytest=False) == "postgresql+psycopg://u:p@h/d"


def test_an_unreadable_password_file_stops_the_service_with_the_path_in_the_message(tmp_path):
    """An unreadable password file raises SecretFileError naming the variable, so the service stops at start."""
    with pytest.raises(SecretFileError, match="SMO_DATABASE_PASSWORD_FILE="):
        resolve_database_url({"SMO_DATABASE_URL": "postgresql+psycopg://smo@db/smo",
                              "SMO_DATABASE_PASSWORD_FILE": str(tmp_path / "gone")}, under_pytest=False)


def test_an_environment_password_is_accepted_too_and_a_url_without_one_is_left_alone():
    """SMO_DATABASE_PASSWORD from the environment is accepted too, and a URL with no password and no secret is unchanged."""
    assert resolve_database_url({"SMO_DATABASE_URL": "postgresql+psycopg://smo@db/smo", "SMO_DATABASE_PASSWORD": "x"},
                                under_pytest=False) == "postgresql+psycopg://smo:x@db/smo"
    assert resolve_database_url({"SMO_DATABASE_URL": "postgresql+psycopg://smo@db/smo"}, under_pytest=False) == \
        "postgresql+psycopg://smo@db/smo"
