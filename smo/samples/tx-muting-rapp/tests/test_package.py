"""The committed CSAR is what the shared builder produces from the sources (the other samples' suites check the same)."""

import importlib.util
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[2]
CSAR = SAMPLES / "tx-muting-rapp.csar"


def _builder():
    spec = importlib.util.spec_from_file_location("build_csar", SAMPLES / "build_csar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_csar_is_up_to_date():
    assert _builder().build_bytes("tx-muting-rapp") == CSAR.read_bytes(), "rebuild: python3 smo/samples/build_csar.py tx-muting-rapp"


def test_csar_holds_the_package_and_nothing_of_the_tooling():
    import io, zipfile
    names = set(zipfile.ZipFile(io.BytesIO(CSAR.read_bytes())).namelist())
    assert {"TOSCA-Metadata/TOSCA.meta", "Definitions/asd.yaml", "manifest.yaml", "capabilities.yaml", "app/main.py",
            "app/engine.py", "app/thresholds.json", "demo.py"} <= names
    assert not [n for n in names if n.startswith(("o1-adaptor-sim", "scripts", "tests")) or n.endswith((".md", ".csar", "docker-compose.yml"))]
