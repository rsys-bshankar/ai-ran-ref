#!/usr/bin/env python3
"""Derives a CM capability descriptor from YANG modules (RFC 7950): the YANG front end
`ingest_cm_schema.py` points at ("a YANG bundle needs a YANG front end"). SA-O1-4.

    cd smo && python scripts/ingest_yang_schema.py \\
        ../specs/O-RAN-WG10-O1NRM-YANGs --name o-ran-wg10-o1nrm \\
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
* A grouping or typedef is found by name across all the input files. `uses` of a
  grouping no input file defines (the 3GPP `_3gpp-common-*` modules are not in this repository) is
  skipped and listed under `unresolved`, so a gap is visible rather than silent; a type
  that cannot be resolved is `any`.
* `revision` is the newest `revision` date across the modules, unless `--revision` is given.

This is a schema reader, not a YANG compiler: it does not evaluate `when` / `must` / `if-feature`,
`deviation` or `refine`, and a node a `deviation` removes stays in the descriptor.
"""

import argparse
import json
import re
from pathlib import Path

INT_TYPES = {"int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"}
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

    def add_module(self, module) -> None:
        self.modules.append(module)
        self._collect(module)

    def _collect(self, stmt) -> None:
        for child in stmt[2]:
            if child[0] == "grouping":
                self.groupings.setdefault(child[1], child)
            elif child[0] == "typedef":
                self.typedefs.setdefault(child[1], child)
            if child[0] in ("grouping", "list", "container", "choice", "case", "augment", "rpc", "notification", "action"):
                self._collect(child)

    # type -> descriptor entry
    def type_of(self, type_stmt, depth: int = 0) -> dict:
        name = _local(type_stmt[1])
        if name in INT_TYPES:
            return {"type": "integer"}
        if name == "decimal64":
            return {"type": "number"}
        if name in ("boolean", "empty"):
            return {"type": "boolean"}
        if name == "enumeration":
            return {"type": "string", "enum": [e[1] for e in _children(type_stmt, "enum")]}
        if name in ("string", "binary", "bits", "identityref", "leafref", "instance-identifier", "union"):
            return {"type": "string"}
        typedef = self.typedefs.get(name)
        if typedef is not None and depth < 10:
            inner = _children(typedef, "type")
            return self.type_of(inner[0], depth + 1) if inner else {"type": "any"}
        return {"type": "any"}

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


def ingest(files: list[Path]) -> Bundle:
    bundle = Bundle()
    for path in files:
        for stmt in parse(path.read_text(errors="replace")):
            if stmt[0] in ("module", "submodule"):
                bundle.add_module(stmt)
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
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    files = yang_files(args.yang)
    bundle = ingest(files)
    descriptor = {
        "schemaName": args.name, "revision": args.revision or bundle.revision(), "type": "YANG",
        "source": [source_name(f) for f in files], "classes": {k: dict(sorted(v.items())) for k, v in sorted(bundle.classes.items())},
        "unresolved": sorted(bundle.unresolved),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(descriptor, indent=1, sort_keys=True) + "\n")
    print(f"wrote {args.out}: {len(descriptor['classes'])} IOCs, revision {descriptor['revision']}, "
          f"{len(descriptor['unresolved'])} unresolved groupings")


if __name__ == "__main__":
    main()
