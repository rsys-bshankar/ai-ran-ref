"""The CM data-model descriptors RAN NF OAM bundles (Wave 9, W9-02) are
generated from the 3GPP NRM definitions in `specs/`, never hand-edited:
this fails if a bundled descriptor no longer matches what
`scripts/ingest_cm_schema.py` (OpenAPI NRM) or `scripts/ingest_yang_schema.py` (YANG, `type` `YANG`) derives from its source — regenerate it with
the command in that script's docstring.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parents[1]
SPECS = SMO_ROOT.parent / "specs" / "5G_APIs"
BUNDLED = sorted((SMO_ROOT / "ran-nf-oam" / "app" / "cm_schemas").glob("*.json"))


def _ingest_module(script: str = "ingest_cm_schema"):
    spec = importlib.util.spec_from_file_location(script, SMO_ROOT / "scripts" / f"{script}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# One case per bundled descriptor file.
# One case per bundled descriptor file.
@pytest.mark.parametrize("path", BUNDLED, ids=[p.name for p in BUNDLED])
def test_bundled_descriptor_matches_its_spec_source(path):
    """Each bundled descriptor equals what the ingest script derives from its source (the YANG ones need `specs/` checked out and are skipped
    without it).
    """
    descriptor = json.loads(path.read_text())
    if descriptor["type"] == "YANG":  # SA-O1-4: sources are paths below specs/
        sources = [SMO_ROOT.parent / "specs" / name for name in descriptor["source"]]
        if not all(s.exists() for s in sources):
            pytest.skip("specs/ not checked out")
        ingest = _ingest_module("ingest_yang_schema")
        library = SMO_ROOT.parent / "specs" / "MnS" / "yang-models"  # SB-3: the 3GPP common modules, definitions only
        if not library.exists():
            pytest.skip("specs/MnS not checked out")
        bundle = ingest.ingest(sources, ingest.yang_files([library]))
        assert descriptor["classes"] == {k: dict(sorted(v.items())) for k, v in sorted(bundle.classes.items())}
        assert descriptor["unresolved"] == sorted(bundle.unresolved) and descriptor["revision"] == bundle.revision()
        assert descriptor.get("library", []) == sorted(bundle.library_used)
        return
    sources = [SPECS / name for name in descriptor["source"]]
    if not all(s.exists() for s in sources):
        pytest.skip("specs/ not checked out")
    assert descriptor["classes"] == _ingest_module().ingest(sources)


def test_the_energy_saving_actuators_are_in_the_spec_descriptor():
    """The NR descriptor has the two energy-saving actuators with their enum values: the cell's administrative state and the energy saving control."""
    classes = json.loads((SMO_ROOT / "ran-nf-oam/app/cm_schemas/3gpp-ts28541-nrnrm.json").read_text())["classes"]
    assert classes["NRCellDU"]["administrativeState"]["enum"] == ["LOCKED", "UNLOCKED"]
    assert classes["CESManagementFunction"]["energySavingControl"]["enum"] == ["TO_BE_ENERGY_SAVING", "TO_BE_NOT_ENERGY_SAVING"]
