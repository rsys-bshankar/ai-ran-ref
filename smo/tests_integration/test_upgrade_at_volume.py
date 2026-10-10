"""scripts/upgrade_at_volume.py (PR-V-6): which revisions come after the previous release's, the table, and the budget."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "upgrade_at_volume.py"
SPEC = importlib.util.spec_from_file_location("upgrade_at_volume", SCRIPT)
upv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upv)


def versions(tmp_path, ids):
    """Creates empty revision files named `<id>_something.py` in a directory (with an `__init__.py`) and returns the directory."""
    for revision in ids:
        (tmp_path / f"{revision}_something.py").write_text("")
    (tmp_path / "__init__.py").write_text("")
    return tmp_path


def test_the_revisions_after_the_previous_release_are_the_ones_to_time(tmp_path):
    """Only the revisions after the previous release's are returned, in order, and none when the release is at the head."""
    folder = versions(tmp_path, ["0026", "0027", "0028", "0029", "0100"])
    assert upv.revisions_after(folder, "0027") == ["0028", "0029", "0100"]
    assert upv.revisions_after(folder, "0100") == []


def test_a_revision_that_is_not_there_stops_the_run(tmp_path):
    """A previous-release revision that is not in the history stops the run (SystemExit) instead of timing nothing."""
    with pytest.raises(SystemExit):
        upv.revisions_after(versions(tmp_path, ["0001"]), "0099")


def test_the_repository_has_the_revisions_the_script_reads():
    """The repository's own history has revision 0027 right after 0026, so the script's real input is read as expected."""
    assert upv.revisions_after(upv.VERSIONS, "0026")[:1] == ["0027"]


class Done:
    def __init__(self, code=0, err=""):
        self.returncode, self.stderr, self.stdout = code, err, ""


def test_each_revision_is_one_timed_migrate_run_and_the_first_failure_stops_the_upgrade():
    """Each revision is one timed migration run, and after the first failure the later revisions are not tried and the failure's note is kept."""
    seen, ticks = [], iter([0.0, 2.0, 2.0, 5.5, 5.5, 6.0])

    def runner(cmd, **kw):
        seen.append(cmd[-1])
        return Done(1, "boom\nsqlalchemy.exc.ProgrammingError: column x") if cmd[-1] == "0029" else Done()

    rows, applied = upv.run_upgrade("postgresql://x", ["0027", "0028", "0029", "0030"], runner, lambda: next(ticks))
    assert seen == ["0027", "0028", "0029"] and not applied                              # 0030 was never tried
    assert [(r, round(s, 1)) for r, s, _ in rows] == [("0027", 2.0), ("0028", 3.5), ("0029", 0.5)]
    assert rows[2][2].startswith("FAILED: ") and "ProgrammingError" in rows[2][2]


def test_the_failure_note_is_the_line_that_names_the_error():
    """The failure note is the line that names the exception (the SQLAlchemy one rather than the driver's), and `no output` for empty output."""
    text = "Traceback (most recent call last):\n  File x\npsycopg.errors.DuplicateTable: relation \"t\" already exists\n\nThe above exception ...\nsqlalchemy.exc.ProgrammingError: (psycopg.errors.DuplicateTable) relation\n(Background on this error at: https://sqlalche.me/e/21/f405)"
    assert upv.failure_line(text).startswith("sqlalchemy.exc.ProgrammingError")
    assert upv.failure_line("") == "no output"


def test_the_table_has_a_row_per_revision_and_the_total(tmp_path):
    """The Markdown table has a row per revision and a total row with the budget."""
    table = upv.markdown([("0027", 1.25, ""), ("0028", 0.5, "")], 1.75, 120)
    assert "| 0027 | 1.2 |" in table or "| 0027 | 1.3 |" in table
    assert "| **all** | **1.8** | budget 120 s |" in table


def test_over_the_budget_fails_and_inside_it_passes(tmp_path, monkeypatch):
    """A run over the budget exits 1 and one inside it exits 0, and no revision to apply is also 1 because the check would prove nothing."""
    folder = versions(tmp_path, ["0001", "0002"])
    monkeypatch.setattr(upv, "VERSIONS", folder)
    monkeypatch.setattr(upv, "run_upgrade", lambda url, revs: ([("0002", 90.0, "")], True))
    assert upv.main(["--url", "x", "--from-revision", "0001", "--budget-seconds", "60"]) == 1
    assert upv.main(["--url", "x", "--from-revision", "0001", "--budget-seconds", "120"]) == 0
    assert upv.main(["--url", "x", "--from-revision", "0002"]) == 1                      # nothing to apply: the check would prove nothing


def test_the_script_runs_as_a_program():
    """The script runs as a program and shows its `--from-revision` option in `--help`."""
    done = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert done.returncode == 0 and "from-revision" in done.stdout
