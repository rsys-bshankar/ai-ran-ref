#!/usr/bin/env python3
"""Generates intent-service/app/ts28312_families.py from the TS 28.312
expectation-family specs in ../specs/5G_APIs (Wave 6, decision D-9).

Each family (RadioNetwork / RadioService / 5GCNetwork / EdgeServiceSupport /
NetworkMaintenance expectation) specialises ExpectationTarget and Context
with named targets/contexts that fix their condition and value range. The
families also accept the generic ExpectationTarget/Context (their oneOf
lists end with the generic type), so the rule enforced at runtime is: a
*known* specialised name must obey its own constraints; any other name is
validated as the generic type.

The service image only carries smo/, not specs/, so the constraints are
generated into a plain Python module rather than read at runtime.
Re-run after updating the spec files:

    cd smo && python scripts/generate_ts28312_families.py
"""

import pprint
from pathlib import Path

import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
SPECS = SMO_ROOT.parent / "specs" / "5G_APIs"
OUT = SMO_ROOT / "intent-service" / "app" / "ts28312_families.py"

# ExpectationObject.objectType -> family file. NetworkMaintenanceExpectation's
# object has no objectType enum of its own; it applies to a SUBNETWORK.
FAMILIES = {
    "RAN_SUBNETWORK": "TS28312_RadioNetworkExpectation.yaml",
    "RADIO_SERVICE": "TS28312_RadioServiceExpectation.yaml",
    "5GC_SUBNETWORK": "TS28312_5GCNetworkExpectation.yaml",
    "EDGE_SERVICE_SUPPORT": "TS28312_EdgeServiceSupportExpectation.yaml",
    "SUBNETWORK": "TS28312_NetworkMaintenanceExpectation.yaml",
}


def _load(name: str) -> dict:
    """Returns `components.schemas` of one TS 28.312 YAML file in `specs/5G_APIs`; raises `KeyError` when the file has no such section."""
    return yaml.safe_load((SPECS / name).read_text())["components"]["schemas"]


NRM = _load("TS28312_IntentNrm.yaml")


def _simplify(schema, local: dict, depth: int = 0):
    """Reduces a JSON schema to the subset the runtime validator checks:
    type / enum / minimum / maximum / items / properties. Local and
    TS28312_IntentNrm refs are followed; other files' refs (TS 28.623
    common types) become "any"."""
    if depth > 6 or not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/components/schemas/"):
            return _simplify(local.get(ref.rsplit("/", 1)[-1], {}), local, depth + 1)
        if ref.startswith("TS28312_IntentNrm.yaml#"):
            return _simplify(NRM.get(ref.rsplit("/", 1)[-1], {}), NRM, depth + 1)
        return {}
    out = {}
    for key in ("type", "enum", "minimum", "maximum"):
        if key in schema:
            out[key] = schema[key]
    if "items" in schema:
        out["items"] = _simplify(schema["items"], local, depth + 1)
    if schema.get("properties"):
        out["properties"] = {k: _simplify(v, local, depth + 1) for k, v in schema["properties"].items()}
    if "oneOf" in schema:
        out["oneOf"] = [_simplify(s, local, depth + 1) for s in schema["oneOf"]]
    return out


def _specialisations(schemas: dict, name_key: str, condition_key: str, value_key: str) -> dict:
    """Collects, from one family's schemas, the specialised targets or contexts: those whose `name_key` property is a single-value enum.

        Returns `{name: {"conditions": [...] | None, "value": simplified value schema}}`. A schema whose name property is absent or has more than one allowed value
        is the generic type and is skipped, which is how the runtime falls back to validating an unknown name as the generic type.
    """
    found = {}
    for schema in schemas.values():
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        names = (props.get(name_key) or {}).get("enum")
        if not names or len(names) != 1:
            continue
        found[names[0]] = {
            "conditions": (props.get(condition_key) or {}).get("enum"),
            "value": _simplify(props.get(value_key) or {}, schemas),
        }
    return found


def main() -> None:
    """Regenerates `intent-service/app/ts28312_families.py` from the five family spec files and prints the (targets, contexts) count per objectType.

        Overwrites the output file unconditionally, so a changed spec shows up as a diff of the generated module. Takes no arguments; reads the YAML under
        `specs/5G_APIs` and needs PyYAML. Does not touch the network or the database.
    """
    families = {}
    for object_type, file_name in FAMILIES.items():
        schemas = _load(file_name)
        families[object_type] = {
            "family": file_name.removeprefix("TS28312_").removesuffix(".yaml"),
            "targets": _specialisations(schemas, "targetName", "targetCondition", "targetValueRange"),
            "contexts": _specialisations(schemas, "contextAttribute", "contextCondition", "contextValueRange"),
        }
    generic_contexts = _specialisations(
        {k: NRM[k] for k in ("SchedulingTimeContext", "DecompositionAssistingContext")},
        "contextAttribute", "contextCondition", "contextValueRange")
    body = (
        '"""GENERATED by scripts/generate_ts28312_families.py from the TS 28.312\n'
        "expectation-family specs — do not edit by hand.\n\n"
        "FAMILIES: ExpectationObject.objectType -> specialised targets/contexts,\n"
        "each with its allowed conditions and simplified value schema.\n"
        'GENERIC_CONTEXTS: TS28312_IntentNrm.yaml\'s own specialised contexts."""\n\n'
        f"FAMILIES = {pprint.pformat(families, width=120, sort_dicts=True)}\n\n"
        f"GENERIC_CONTEXTS = {pprint.pformat(generic_contexts, width=120, sort_dicts=True)}\n"
    )
    OUT.write_text(body)
    counts = {k: (len(v["targets"]), len(v["contexts"])) for k, v in families.items()}
    print(f"wrote {OUT.relative_to(SMO_ROOT)}: (targets, contexts) per objectType {counts}")


if __name__ == "__main__":
    main()
