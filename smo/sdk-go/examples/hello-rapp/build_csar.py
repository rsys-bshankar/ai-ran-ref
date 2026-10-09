#!/usr/bin/env python3
"""Zips the example's `package/` directory into hello-go-rapp.csar with the same rules as `samples/build_csar.py`
(sorted entries, fixed timestamps, so a rebuild of unchanged sources is byte-identical; it IS that script's `build_bytes`).

    python3 smo/sdk-go/examples/hello-rapp/build_csar.py [output-dir]      # default: the current directory

The CSAR carries the manifest (with the `operatorUi` page), the capabilities and the ASD. The Go binary is not in it: the
rApp runs as a container (see the README), as the sample rApps run as compose services. The .csar is a build product and is
not committed; `tests_integration/test_sdk_go_example.py` builds it and checks it passes Onboarding's checks.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "samples"))

import build_csar  # noqa: E402  (samples/build_csar.py)

NAME = "hello-go-rapp"
PACKAGE = HERE / "package"


def build_bytes() -> bytes:
    # build_bytes() joins SAMPLES_DIR / name: a relative path out of samples/ is the package directory
    return build_csar.build_bytes(str(Path("..") / PACKAGE.relative_to(HERE.parents[2])))


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".") / f"{NAME}.csar"
    out.write_bytes(build_bytes())
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
