#!/usr/bin/env python3
"""A module's OpenAPI document as the gateway serves it, for the authenticated DAST scan (PR-V-7d).

    scripts/dast_gateway_spec.py docs/openapi/sme.json /sme out/sme.json

The gateway forwards `/<prefix>/<path>` to the module's `/<path>` (r1-termination/app/main.py), and the module's own document names its paths with no prefix and no `servers`.
The copy has every path under the prefix, so a scanner given it and the gateway's address calls the module through the gateway, with a token. Nothing else changes.
"""

import json
import sys
from pathlib import Path


def through_gateway(spec: dict, prefix: str) -> dict:
    """A copy of the OpenAPI document with every path moved under `/<prefix>` and `servers` removed, so a scanner pointed at the gateway reaches the module through its route."""
    prefix = "/" + prefix.strip("/")
    out = dict(spec)
    out["paths"] = {prefix + path: item for path, item in spec["paths"].items()}
    out.pop("servers", None)
    return out


def main(argv: list[str]) -> int:
    """Writes the through-the-gateway copy of the spec at `argv[0]` to `argv[2]` for the prefix `argv[1]`. Returns 2 and prints the usage when the argument count is wrong."""
    if len(argv) != 3:
        print(__doc__)
        return 2
    spec_path, prefix, out_path = argv
    spec = json.loads(Path(spec_path).read_text())
    Path(out_path).write_text(json.dumps(through_gateway(spec, prefix), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
