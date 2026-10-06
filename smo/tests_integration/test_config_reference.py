"""The configuration reference lists every environment variable the code reads (PR-OPS-7.3).

`scripts/config_reference.py` walks the source with `ast` and `docs/CONFIGURATION.md` is generated from it plus the hand-written
`docs/config_descriptions.json`. A variable read in code and absent from the table fails here, and so does a row no code reads; the seeded-miss
tests below run a synthetic snippet through the same extraction function and show that it would.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("config_reference", SMO_ROOT / "scripts" / "config_reference.py")
assert _spec and _spec.loader
cr = importlib.util.module_from_spec(_spec)
sys.modules["config_reference"] = cr
_spec.loader.exec_module(cr)

DOC = (SMO_ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def walked():
    reads, unresolved = cr.collect(SMO_ROOT)
    return reads, unresolved


def undocumented(reads, document: str) -> list[str]:
    """Names read in code that the document's table does not list."""
    return sorted({r.name for r in reads} - set(cr.documented_names(document)))


def unread(reads, document: str) -> list[str]:
    """Names the document's table lists that no code reads."""
    return sorted(set(cr.documented_names(document)) - {r.name for r in reads})


def test_every_variable_read_in_code_is_in_the_table(walked):
    reads, _ = walked
    assert len({r.name for r in reads}) > 100, "the walk found almost nothing: it is not reading the source"
    assert undocumented(reads, DOC) == [], "read in code, missing from docs/CONFIGURATION.md: run python scripts/config_reference.py --write"


def test_the_table_lists_no_variable_that_no_code_reads(walked):
    reads, _ = walked
    assert unread(reads, DOC) == [], "listed in docs/CONFIGURATION.md but read nowhere: run python scripts/config_reference.py --write"


def test_no_read_has_a_name_the_walk_cannot_work_out(walked):
    _, unresolved = walked
    assert unresolved == [], "an environment read whose variable name is built in a way the walk cannot follow: add `# config-ref: NAME` on or above it"


def test_every_variable_has_a_description_and_no_description_is_orphaned(walked):
    reads, _ = walked
    variables = cr.build(reads)
    assert [v.name for v in variables if v.description == "TODO"] == []
    assert sorted(set(cr.load_descriptions()) - {v.name for v in variables}) == []


def test_the_documented_defaults_are_the_code_defaults(walked):
    reads, _ = walked
    documented = cr.documented_names(DOC)
    drifted = {v.name: (documented[v.name], cr.default_cell(v)) for v in cr.build(reads) if documented.get(v.name) != cr.default_cell(v)}
    assert drifted == {}, "a default changed in code: run python scripts/config_reference.py --write"


def test_the_committed_document_is_what_the_generator_writes(walked):
    reads, _ = walked
    assert cr.splice(DOC, cr.render_markdown(cr.build(reads))) == DOC, "docs/CONFIGURATION.md is stale: run python scripts/config_reference.py --write"


def test_secrets_are_marked(walked):
    reads, _ = walked
    secret = {v.name for v in cr.build(reads) if v.secret}
    assert {"SMO_DATABASE_PASSWORD", "SMO_DATABASE_PASSWORD_FILE", "SMO_ENROLLMENT_SECRET", "GUI_JWT_SECRET", "GUI_ADMIN_PASSWORD"} <= secret
    assert "R1_RATE_PER_SECOND" not in secret and "IDEMPOTENCY_KEY_TTL_SECONDS" not in secret     # a description may say a name is not one


# ---------------------------------------------------------------- the extraction, on snippets

SNIPPET = '''
import os
from smo_shared.secretfile import read_secret

NAMED = "SEEDED_CONSTANT_NAME"
HOURS = 6


def timeout(name, default):
    return float(os.environ.get(name, default))


def settings(prefix):
    return int(os.environ.get(f"{prefix}_LIMIT", 5))


class Settings:
    port = int(os.environ.get("SEEDED_PORT", "8000"))
    required = os.environ["SEEDED_REQUIRED"]
    viaenv = os.getenv("SEEDED_GETENV", 3 * HOURS)
    named = os.environ.get(NAMED)
    password = read_secret("SEEDED_PASSWORD")
    present = "SEEDED_PRESENT" in os.environ
    guarded = os.environ.get("SEEDED_GUARDED") or "fallback"
    wrapped = timeout("SEEDED_WRAPPED_SECONDS", 2.5)
    prefixed = settings("SEEDED_PREFIX")
'''


def reads_of(source: str):
    reads, unresolved = cr.extract(source)
    return {r.name: r for r in reads}, unresolved


def test_extraction_finds_each_way_of_reading_the_environment():
    found, unresolved = reads_of(SNIPPET)
    assert unresolved == []
    assert {"SEEDED_PORT", "SEEDED_REQUIRED", "SEEDED_GETENV", "SEEDED_CONSTANT_NAME", "SEEDED_PASSWORD", "SEEDED_PASSWORD_FILE", "SEEDED_PRESENT",
            "SEEDED_GUARDED", "SEEDED_WRAPPED_SECONDS", "SEEDED_PREFIX_LIMIT"} == set(found)
    assert found["SEEDED_PORT"].default == "8000"
    assert found["SEEDED_REQUIRED"].default == "required"
    assert found["SEEDED_GETENV"].default == "18"                       # folded from a module constant
    assert found["SEEDED_CONSTANT_NAME"].default == "unset"
    assert found["SEEDED_GUARDED"].default == "fallback"                # `or "fallback"` is the default
    assert found["SEEDED_WRAPPED_SECONDS"].default == "2.5"             # the wrapper's call site, with its own default
    assert found["SEEDED_PREFIX_LIMIT"].default == "5"
    assert found["SEEDED_PASSWORD"].secret_helper and found["SEEDED_PASSWORD_FILE"].secret_helper


def test_the_wrapper_itself_is_not_a_variable():
    found, _ = reads_of(SNIPPET)
    assert not any(n.startswith("<") for n in found)        # neither `name` nor `{prefix}` is reported as a variable of its own


def test_a_name_built_at_run_time_is_a_pattern():
    found, unresolved = reads_of('import os\n\ndef cred(ref):\n    return os.environ.get(f"SEEDED_CRED_{ref.upper()}_PASSWORD")\n')
    assert unresolved == [] and set(found) == {"SEEDED_CRED_<REF>_PASSWORD"}


def test_a_name_the_walk_cannot_follow_must_be_annotated():
    source = 'import os\n\nfor var in ("A", "B"):\n    print(os.environ.get(var))\n'
    assert reads_of(source)[1] == ["snippet.py:4"]
    annotated = 'import os\n\nfor var in ("A", "B"):\n    # config-ref: SEEDED_A, SEEDED_B\n    print(os.environ.get(var))\n'
    found, unresolved = reads_of(annotated)
    assert unresolved == [] and set(found) == {"SEEDED_A", "SEEDED_B"}


def test_a_compose_substitution_is_a_read():
    reads = cr.compose_reads("services:\n  a:\n    environment:\n      X: ${SEEDED_COMPOSE:-7}\n      # ${COMMENTED_OUT:-1}\n")
    assert [(r.name, r.default, r.module) for r in reads] == [("SEEDED_COMPOSE", "7", "compose")]


# ---------------------------------------------------------------- the seeded miss


def test_a_new_environment_read_that_is_not_in_the_table_fails_the_check(walked):
    reads, _ = walked
    new, _ = cr.extract('import os\nPOOL = os.environ.get("SEEDED_MISS_NEW_SETTING", "1")\n', "billing/app/main.py", "billing")
    assert undocumented(reads, DOC) == []                                # the real tree passes ...
    assert undocumented([*reads, *new], DOC) == ["SEEDED_MISS_NEW_SETTING"]     # ... and the same tree plus one new read does not


def test_a_row_for_a_variable_no_code_reads_fails_the_check(walked):
    reads, _ = walked
    stale = DOC.replace("### Read by several modules", "| `SEEDED_STALE_ROW` | `1` |  | `x.py` | gone |\n\n### Read by several modules", 1)
    assert unread(reads, DOC) == [] and unread(reads, stale) == ["SEEDED_STALE_ROW"]
