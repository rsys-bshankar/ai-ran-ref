"""The CM data-model descriptors RAN NF OAM bundles (Wave 9, W9-02) are
generated from the 3GPP NRM definitions in `specs/`, never hand-edited:
this fails if a bundled descriptor no longer matches what
`scripts/ingest_cm_schema.py` derives from its source — regenerate it with
the command in that script's docstring.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parents[1]
SPECS = SMO_ROOT.parent / "specs" / "5G_APIs"
BUNDLED = sorted((SMO_ROOT / "ran-nf-oam" / "app" / "cm_schemas").glob("*.json"))


def _ingest_module():
    spec = importlib.util.spec_from_file_location("ingest_cm_schema", SMO_ROOT / "scripts" / "ingest_cm_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path", BUNDLED, ids=[p.name for p in BUNDLED])
def test_bundled_descriptor_matches_its_spec_source(path):
    descriptor = json.loads(path.read_text())
    sources = [SPECS / name for name in descriptor["source"]]
    if not all(s.exists() for s in sources):
        pytest.skip("specs/ not checked out")
    assert descriptor["classes"] == _ingest_module().ingest(sources)


def test_the_energy_saving_actuators_are_in_the_spec_descriptor():
    classes = json.loads((SMO_ROOT / "ran-nf-oam/app/cm_schemas/3gpp-ts28541-nrnrm.json").read_text())["classes"]
    assert classes["NRCellDU"]["administrativeState"]["enum"] == ["LOCKED", "UNLOCKED"]
    assert classes["CESManagementFunction"]["energySavingControl"]["enum"] == ["TO_BE_ENERGY_SAVING", "TO_BE_NOT_ENERGY_SAVING"]
