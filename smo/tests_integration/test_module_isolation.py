"""Each module runs as its own process (its own container in
docker-compose.yml), so its ORM metadata holds only its own tables. A
ForeignKey declared in one module's models.py against another module's
table can't resolve there: SQLAlchemy raises NoReferencedTableError on the
first flush that touches the table — which is how NFO's CreateDescriptor,
Onboarding's descriptor write-back and rApp Management's CreateInstance all
failed on a real deployment. Every other test hides this: the unit suites
stand in the foreign tables, and this directory's harness loads every module
into ONE process with one shared metadata. So this test imports each
module's models in a fresh interpreter, the way it actually runs, and
resolves every ForeignKey there. Cross-module references are enforced by
the consolidated migration (migrations/001_init.sql) at the DB level.
Run with: PYTHONPATH=shared pytest tests_integration -q
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parent.parent
MODULES_WITH_MODELS = sorted(p.parent.parent.name for p in SMO_ROOT.glob("*/app/models.py"))

_RESOLVE_ALL_FKS = (
    "import app.models\n"
    "from smo_shared.db import Base\n"
    "for table in Base.metadata.sorted_tables:\n"
    "    for fk in table.foreign_keys:\n"
    "        fk.column\n"
)


@pytest.mark.parametrize("module", MODULES_WITH_MODELS)
def test_every_orm_foreign_key_resolves_within_its_own_module(module):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SMO_ROOT / module), str(SMO_ROOT / "shared")]),
           "SMO_DATABASE_URL": "sqlite://"}
    result = subprocess.run([sys.executable, "-c", _RESOLVE_ALL_FKS], cwd=SMO_ROOT / module, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr.strip().splitlines()[-1]
