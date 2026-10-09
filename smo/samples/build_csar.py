#!/usr/bin/env python3
"""Zips a sample rApp directory into <name>.csar — the actual package
Onboarding's `_validate_package` fetches over HTTP and opens.

    python3 smo/samples/build_csar.py                      # every sample
    python3 smo/samples/build_csar.py energy-saving-rapp   # one sample
    python3 smo/samples/build_csar.py --source-dir <dir> --name <name>   # any package directory -> ./<name>.csar
                                                           # (the Java example: sdk-java/README.md)

Test suites, caches, the sample's own service tests and its README.md are left out, and
every entry carries a fixed timestamp, so a rebuild of unchanged sources is
byte-identical.

See smo/DEMO_RUNBOOK.md for how to serve the resulting .csar file and
walk through the full rApp lifecycle against a running `docker compose
up` stack.
"""

import io
import sys
import zipfile
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent
SAMPLES = ["energy-saving-rapp", "mobility-optimization-rapp", "coverage-optimization-rapp",
           "traffic-steering-rapp"]
EXCLUDED_PARTS = {"__pycache__", "tests", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDED_FILES = {"README.md"}  # sample documentation, not package content
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def build_bytes(name: str, source: Path | None = None) -> bytes:
    """The CSAR of `samples/<name>/`, or of the directory `source` when given."""
    source = source or SAMPLES_DIR / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(source)
            if path.is_file() and not EXCLUDED_PARTS & set(rel.parts) and rel.as_posix() not in EXCLUDED_FILES:
                info = zipfile.ZipInfo(rel.as_posix(), FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, path.read_bytes())
    return buf.getvalue()


def build(name: str) -> Path:
    output = SAMPLES_DIR / f"{name}.csar"
    output.write_bytes(build_bytes(name))
    print(f"wrote {output.relative_to(SAMPLES_DIR.parent.parent)}")
    return output


def build_dir(source: Path, name: str) -> Path:
    """A package directory outside `samples/` (the Java SDK's example): `./<name>.csar`, built the same way."""
    output = Path.cwd() / f"{name}.csar"
    output.write_bytes(build_bytes(name, source.resolve()))
    print(f"wrote {output}")
    return output


def main() -> None:
    args = sys.argv[1:]
    if "--source-dir" in args:
        if "--name" not in args or args.index("--source-dir") + 1 >= len(args) or args.index("--name") + 1 >= len(args):
            sys.exit("usage: build_csar.py --source-dir <dir> --name <name>")
        build_dir(Path(args[args.index("--source-dir") + 1]), args[args.index("--name") + 1])
        return
    for name in args or SAMPLES:
        build(name)


if __name__ == "__main__":
    main()
