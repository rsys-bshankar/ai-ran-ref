#!/usr/bin/env python3
"""Derives a CM capability descriptor from YANG modules (RFC 7950): the YANG front end
`ingest_cm_schema.py` points at ("a YANG bundle needs a YANG front end"). SA-O1-4.

    cd smo && python scripts/ingest_yang_schema.py \\
        ../specs/O-RAN-WG10-O1NRM-YANGs --name o-ran-wg10-o1nrm \\
        --library ../specs/MnS/yang-models \\
        --out ran-nf-oam/app/cm_schemas/o-ran-wg10-o1nrm.json

Output: the same descriptor shape `ingest_cm_schema.py` writes (and `POST /ran-nf-oam/cm-schemas`
accepts), `{"classes": {"<IOC>": {"<attribute>": {"type": ..., "enum"?: [...]}}}}`:

* An IOC is a `list` or `container` data node at the top of a module or inside an `augment` (a
  list or container nested in an IOC is one of its attributes, `array` / `object`). Its attributes
  are its `leaf` / `leaf-list` children (through `uses`, `choice` and `case`) and, in the 3GPP
  convention the O1 NRM modules follow, those of its `container attributes`.
* `type`: integers -> `integer`, `decimal64` -> `number`, `boolean` and `empty` -> `boolean`,
  `enumeration` -> `string` with `enum`, a `leaf-list` -> `array`; a typedef is followed to its
  base type; everything else (`string`, `identityref`, `leafref`, `union`, `bits`, ...) is `string`.
* Constraints (SB-5.1, checked by `ran-nf-oam/app/leafcheck.py`): an integer carries `range`, a list of
  `[lo, hi]` intervals (its own `range`, else its typedef's, else the native bounds of `uint8` ... `int64`);
  a `decimal64` carries `fractionDigits` and, when restricted, `range`; a `string` carries `length` (intervals,
  `null` = unbounded) and `pattern` (a list: a typedef's patterns and the leaf's own all apply). The most
  derived `range` / `length` replaces the inherited one. `min` / `max` are the type's bounds; `invert-match`
  patterns are not captured.
* A grouping or typedef is found by name across all the input files, then across the `--library`
  files. A library is where definitions come from without contributing classes of its own: the 3GPP SA5
  YANG (`specs/MnS/yang-models`: `_3gpp-common-top`, `-managed-function`, `-ep-rp`, `-yang-types`, ...)
  that the O-RAN modules import, so the attributes they contribute (`id`, `userLabel`, `EP_Common`, ...)
  are in the descriptor without every 3GPP IOC being too. A definition in an input file wins over one of
  the same name in the library. `uses` of a grouping nothing defines is skipped and listed under
  `unresolved`, so a gap is visible rather than silent; a type that cannot be resolved is `any`. The
  library files that supplied something are listed under `library` in the descriptor.
* `revision` is the newest `revision` date across the modules, unless `--revision` is given.

This is a schema reader, not a YANG compiler: it does not evaluate `when` / `must` / `if-feature`,
`deviation` or `refine`, and a node a `deviation` removes stays in the descriptor.
"""

import argparse
import json
import re
from pathlib import Path

INT_TYPES = {"int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"}
INT_BOUNDS = {"int8": (-2**7, 2**7 - 1), "int16": (-2**15, 2**15 - 1), "int32": (-2**31, 2**31 - 1), "int64": (-2**63, 2**63 - 1),
              "uint8": (0, 2**8 - 1), "uint16": (0, 2**16 - 1), "uint32": (0, 2**32 - 1), "uint64": (0, 2**64 - 1)}
DECIMAL64_MAX = 2**63 - 1


def _number(text: str, decimal: bool):
    text = text.strip().lstrip("+")
    if decimal:
        return float(text)
    return int(text, 16) if text.lower().startswith(("0x", "-0x")) else int(text)


def parse_intervals(text: str, low, high, decimal: bool = False) -> list[list]:
    """A YANG `range` / `length` argument ("0..100 | 200", "min..max", "-90.0..+90.0") as [[lo, hi], ...];
    `min` and `max` become `low` and `high`, which may be None (unbounded)."""
    out = []
    for part in text.split("|"):
        bounds = [b.strip() for b in part.split("..")]
        ends = [low if b == "min" else high if b == "max" else _number(b, decimal) for b in bounds if b != ""]
        if not ends:
            continue
        out.append([ends[0], ends[-1]])
    return out
DATA_NODES = {"leaf", "leaf-list", "list", "container", "choice", "anydata", "anyxml"}


# ---------------------------------------------------------------- parsing

def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "{};+":
            yield c, False
            i += 1
        elif c in "\"'":
            j = i + 1
            buf = []
            while j < n and text[j] != c:
                if c == '"' and text[j] == "\\" and j + 1 < n:
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(text[j + 1], text[j + 1]))
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            yield "".join(buf), True
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "{};":
                j += 1
            yield text[i:j], False
            i = j


def parse(text: str) -> list:
    """Statements as (keyword, argument, [children])."""
    toks = list(_tokens(text))
    pos = 0

    def statements() -> list:
        nonlocal pos
        out = []
        while pos < len(toks) and toks[pos] != ("}", False):
            keyword = toks[pos][0]
            pos += 1
            arg = None
            if pos < len(toks) and toks[pos] not in (("{", False), (";", False)):
                arg = toks[pos][0]
                pos += 1
                while pos + 1 < len(toks) and toks[pos] == ("+", False):  # "a" + "b"
                    arg += toks[pos + 1][0]
                    pos += 2
            children = []
            if pos < len(toks) and toks[pos] == ("{", False):
                pos += 1
                children = statements()
                pos += 1  # }
            elif pos < len(toks) and toks[pos] == (";", False):
                pos += 1
            out.append((keyword, arg, children))
        return out

    return statements()


def _children(stmt, *keywords):
    return [c for c in stmt[2] if c[0] in keywords]


def _local(name: str) -> str:
    return name.split(":", 1)[-1]


# ---------------------------------------------------------------- the model

class Bundle:
    def __init__(self):
        self.groupings: dict[str, tuple] = {}
        self.typedefs: dict[str, tuple] = {}
        self.modules: list[tuple] = []
        self.classes: dict[str, dict] = {}
        self.unresolved: set[str] = set()
        self.library_origin: dict[int, str] = {}  # id(grouping or typedef statement) -> the library file it came from
        self.library_used: set[str] = set()

    def add_module(self, module) -> None:
        self.modules.append(module)
        self._collect(module)

    def add_library_module(self, module, origin: str) -> None:
        """Its groupings and typedefs can be used; its own data nodes are not classes."""
        self._collect(module, origin)

    def _collect(self, stmt, origin: str | None = None) -> None:
        for child in stmt[2]:
            if child[0] == "grouping":
                if child[1] not in self.groupings:
                    self.groupings[child[1]] = child
                    if origin:
                        self.library_origin[id(child)] = origin
            elif child[0] == "typedef":
                if child[1] not in self.typedefs:
                    self.typedefs[child[1]] = child
                    if origin:
                        self.library_origin[id(child)] = origin
            if child[0] in ("grouping", "list", "container", "choice", "case", "augment", "rpc", "notification", "action"):
                self._collect(child, origin)

    def _used(self, stmt) -> None:
        origin = self.library_origin.get(id(stmt))
        if origin:
            self.library_used.add(origin)

    # type -> descriptor entry
    def type_of(self, type_stmt, depth: int = 0) -> dict:
        name = _local(type_stmt[1])
        own = {c[0]: c for c in _children(type_stmt, "range", "length", "fraction-digits")}
        patterns = [c[1] for c in _children(type_stmt, "pattern")
                    if not any(m[0] == "modifier" and m[1] == "invert-match" for m in c[2])]
        if name in INT_TYPES:
            entry = {"type": "integer", "range": [list(INT_BOUNDS[name])]}
        elif name == "decimal64":
            entry = {"type": "number"}
        elif name in ("boolean", "empty"):
            return {"type": "boolean"}
        elif name == "enumeration":
            return {"type": "string", "enum": [e[1] for e in _children(type_stmt, "enum")]}
        elif name == "string":
            entry = {"type": "string"}
        elif name in ("binary", "bits", "identityref", "leafref", "instance-identifier", "union"):
            return {"type": "string"}
        else:
            typedef = self.typedefs.get(name)
            if typedef is None or depth >= 10:
                return {"type": "any"}
            self._used(typedef)
            inner = _children(typedef, "type")
            entry = self.type_of(inner[0], depth + 1) if inner else {"type": "any"}
        # this statement's own restrictions sit on top of what the base or typedef gave
        if name == "decimal64" and "fraction-digits" in own:
            entry["fractionDigits"] = int(own["fraction-digits"][1])
        if entry["type"] in ("integer", "number") and "range" in own:
            entry_bounds = entry.get("range") or [[None, None]]
            low, high = min((r[0] for r in entry_bounds), default=None), max((r[1] for r in entry_bounds), default=None)
            if entry["type"] == "number":
                digits = entry.get("fractionDigits")
                limit = DECIMAL64_MAX / 10 ** digits if digits is not None else None
                low, high = (-limit if limit else None), limit
            entry["range"] = parse_intervals(own["range"][1], low, high, decimal=entry["type"] == "number")
        if entry["type"] == "string":
            if "length" in own:
                entry["length"] = parse_intervals(own["length"][1], 0, None)
            if patterns:
                entry["pattern"] = entry.get("pattern", []) + patterns
        return entry

    def attributes(self, node, seen=frozenset()) -> dict:
        attrs: dict[str, dict] = {}
        for child in node[2]:
            keyword = child[0]
            if keyword == "leaf":
                types = _children(child, "type")
                attrs[child[1]] = self.type_of(types[0]) if types else {"type": "any"}
            elif keyword == "leaf-list":
                attrs[child[1]] = {"type": "array"}
            elif keyword in ("list", "container"):
                if child[1] == "attributes":  # the 3GPP IOC convention: the attributes sit in `container attributes`
                    attrs.update(self.attributes(child, seen))
                else:
                    attrs[child[1]] = {"type": "array" if keyword == "list" else "object"}
            elif keyword in ("anydata", "anyxml"):
                attrs[child[1]] = {"type": "any"}
            elif keyword == "uses":
                name = _local(child[1])
                grouping = self.groupings.get(name)
                if grouping is None:
                    self.unresolved.add(child[1])
                elif name not in seen:
                    self._used(grouping)
                    attrs.update(self.attributes(grouping, seen | {name}))
            elif keyword in ("choice", "case"):
                attrs.update(self.attributes(child, seen))
        return attrs

    def add_class(self, node) -> None:
        attrs = self.attributes(node)
        existing = self.classes.setdefault(node[1], {})
        existing.update(attrs)

    def build(self) -> None:
        for module in self.modules:
            self._walk(module)

    def _walk(self, stmt) -> None:
        for child in stmt[2]:
            if child[0] in ("list", "container"):
                self.add_class(child)
            elif child[0] == "augment":
                self._walk(child)

    def revision(self) -> str:
        dates = [r[1] for m in self.modules for r in _children(m, "revision")]
        return max(dates) if dates else ""


def ingest(files: list[Path], library: list[Path] | None = None) -> Bundle:
    """`files` are the schema's own modules (their IOCs are the descriptor's classes, their definitions win);
    `library` modules only supply groupings and typedefs the first can `uses`."""
    bundle = Bundle()
    for path in files:
        for stmt in parse(path.read_text(errors="replace")):
            if stmt[0] in ("module", "submodule"):
                bundle.add_module(stmt)
    for path in library or []:
        for stmt in parse(path.read_text(errors="replace")):
            if stmt[0] in ("module", "submodule"):
                bundle.add_library_module(stmt, source_name(path))
    bundle.build()
    return bundle


def yang_files(paths: list[Path]) -> list[Path]:
    files = []
    for p in paths:
        files += sorted(p.rglob("*.yang")) if p.is_dir() else [p]
    return files


def source_name(path: Path) -> str:
    """The file's path below the `specs/` directory (so two files of one name stay distinct)."""
    parts = path.resolve().parts
    return "/".join(parts[parts.index("specs") + 1:]) if "specs" in parts else path.name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("yang", nargs="+", type=Path, help="YANG file(s) or directories")
    parser.add_argument("--name", required=True)
    parser.add_argument("--revision", help="default: the newest revision date of the modules")
    parser.add_argument("--library", nargs="+", type=Path, default=[], metavar="YANG",
                        help="YANG file(s) or directories that only supply groupings and typedefs (e.g. the 3GPP common modules)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    files = yang_files(args.yang)
    bundle = ingest(files, yang_files(args.library))
    descriptor = {
        "schemaName": args.name, "revision": args.revision or bundle.revision(), "type": "YANG",
        "source": [source_name(f) for f in files], "classes": {k: dict(sorted(v.items())) for k, v in sorted(bundle.classes.items())},
        "unresolved": sorted(bundle.unresolved),
    }
    if bundle.library_used:
        descriptor["library"] = sorted(bundle.library_used)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(descriptor, indent=1, sort_keys=True) + "\n")
    print(f"wrote {args.out}: {len(descriptor['classes'])} IOCs, revision {descriptor['revision']}, "
          f"{len(descriptor['unresolved'])} unresolved groupings")


if __name__ == "__main__":
    main()
