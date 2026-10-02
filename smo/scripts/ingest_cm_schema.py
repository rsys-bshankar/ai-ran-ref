#!/usr/bin/env python3
"""Derives a CM capability descriptor from 3GPP NRM OpenAPI definitions —
Wave 9 (HISTORY.md W9-02), step 4 of
`docs/ARCHITECTURE.md`'s sketch: the descriptor a
vendor's `CMSchemaCache` entry points at is generated mechanically from a
source of truth, never hand-transcribed.

Input: one or more NRM files in the TS 28.5xx/28.623 stage-3 OpenAPI shape
(`specs/5G_APIs/TS28541_NrNrm.yaml`, ...). Every `<IOC>-Single` schema is an
IOC; its `attributes` (following `allOf` and `$ref`, across sibling files) are
the attributes a CM write may set. Output: a JSON descriptor

    {"schemaName", "revision", "type": "OPENAPI_NRM", "source": [...],
     "classes": {"<IOC>": {"<attribute>": {"type": ..., "enum"?: [...]}}}}

which `POST /ran-nf-oam/cm-schemas` accepts as `descriptor`, and which
`ran-nf-oam/app/cm_schemas/` bundles for the 3GPP spec itself (the SPEC
descriptor every `SPEC`/`COMBINED` vendor validates against).

    cd smo && python scripts/ingest_cm_schema.py ../specs/5G_APIs/TS28541_NrNrm.yaml \\
        --name 3gpp-ts28541-nrnrm --revision 19.4.0 \\
        --out ran-nf-oam/app/cm_schemas/3gpp-ts28541-nrnrm.json

A vendor's own data model (`OWN`/`COMBINED`) goes through the same script
when it is published as NRM OpenAPI; a YANG bundle needs a YANG front end
(pyang) emitting the same descriptor shape — see the guide's "what this
does not solve".
"""

import argparse
import json
from pathlib import Path

import yaml

_docs: dict[Path, dict] = {}


def _load(path: Path) -> dict:
    path = path.resolve()
    if path not in _docs:
        _docs[path] = yaml.safe_load(path.read_text())
    return _docs[path]


def _resolve(ref: str, base: Path) -> tuple[dict, Path]:
    file_part, _, pointer = ref.partition("#")
    path = (base.parent / file_part) if file_part else base
    if not path.exists():
        return {}, path  # a sibling spec this checkout doesn't carry — treated as opaque
    node = _load(path)
    for part in pointer.strip("/").split("/"):
        if part:
            node = node.get(part, {})
    return node, path


def _attributes(schema: dict, base: Path, seen: frozenset = frozenset()) -> dict[str, tuple[dict, Path]]:
    """Every property reachable through allOf/$ref, with the file each is defined in."""
    if "$ref" in schema:
        key = (str(base), schema["$ref"])
        if key in seen:
            return {}
        target, path = _resolve(schema["$ref"], base)
        return _attributes(target, path, seen | {key})
    out: dict[str, tuple[dict, Path]] = {}
    for part in schema.get("allOf", []):
        out.update(_attributes(part, base, seen))
    for name, prop in (schema.get("properties") or {}).items():
        out[name] = (prop, base)
    return out


def _type_of(prop: dict, base: Path, depth: int = 0) -> dict:
    if "$ref" in prop and depth < 8:
        target, path = _resolve(prop["$ref"], base)
        return _type_of(target, path, depth + 1) if target else {"type": "object"}
    if "allOf" in prop and len(prop["allOf"]) == 1 and depth < 8:
        return _type_of(prop["allOf"][0], base, depth + 1)
    out = {"type": prop.get("type", "object" if ("properties" in prop or "allOf" in prop) else "any")}
    if "enum" in prop:
        out["enum"] = list(prop["enum"])
    return out


def ingest(paths: list[Path]) -> dict:
    classes: dict[str, dict] = {}
    for path in paths:
        schemas = _load(path).get("components", {}).get("schemas", {})
        for name, schema in schemas.items():
            if not name.endswith("-Single"):
                continue
            ioc = name[: -len("-Single")]
            props = _attributes(schema, path)
            attrs_schema = props.get("attributes")
            if attrs_schema is None:
                continue
            attrs = _attributes(attrs_schema[0], attrs_schema[1])
            classes[ioc] = {attr: _type_of(prop, where) for attr, (prop, where) in sorted(attrs.items())}
    return classes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("nrm", nargs="+", type=Path, help="NRM OpenAPI file(s)")
    parser.add_argument("--name", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    descriptor = {
        "schemaName": args.name, "revision": args.revision, "type": "OPENAPI_NRM",
        "source": [p.name for p in args.nrm], "classes": ingest(args.nrm),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(descriptor, indent=1, sort_keys=True) + "\n")
    print(f"wrote {args.out}: {len(descriptor['classes'])} IOCs")


if __name__ == "__main__":
    main()
