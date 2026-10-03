"""The managed-object containment tree of RAN NF OAM (PR-SB-6, SA-RANOAM-4).

One row per managed object, keyed by its distinguished name (TS 32.300 DN syntax, `ldn.py`): `ManagedElement=ME-1`, then its descendants
`ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101`. Each row names its parent, so the containment is a tree of the registry's own making
(`source=registry`: the element root and the managed function an element was registered with) and, once PR-SB-6.2 walks a server, of what the
server reports (`source=walk`).

An element is registered under a flat key (`ME-1`): its root DN is `ManagedElement=ME-1`. A key that is itself a DN is its own root. A
managed function ref is the DN *below* the root (`GNBDUFunction=1,NRCellDU=101`), the way the write routes already take it; one that starts with
`ManagedElement=` is taken as a full DN.

DNs are compared as exact text: case-sensitive, no whitespace around the RDNs.
"""

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ldn import parse_ldn
from .models import ManagedObject

MAX_SUBTREE_NODES = 1000
MAX_SUBTREE_DEPTH = 16


def root_dn(managed_element_ref: str) -> str:
    """The DN of an element's root object."""
    return managed_element_ref if "=" in managed_element_ref else f"ManagedElement={managed_element_ref}"


def target_dn(managed_element_ref: str, managed_function_ref: str | None) -> str:
    """The DN a write targets: the element root, or the function below it (a full DN, starting `ManagedElement=`, is taken as given)."""
    root = root_dn(managed_element_ref)
    if not managed_function_ref:
        return root
    if "=" not in managed_function_ref:                              # a flat function id (`101`): no class to put it under, so it is the root's
        return root
    return managed_function_ref if managed_function_ref.startswith("ManagedElement=") else f"{root},{managed_function_ref}"


def ancestors(dn: str) -> list[str]:
    """Every DN from the root down to `dn` itself: `A=1,B=2,C=3` gives `A=1`, `A=1,B=2`, `A=1,B=2,C=3`."""
    rdns = [f"{c}={i}" for c, i in parse_ldn(dn)]
    return [",".join(rdns[: n + 1]) for n in range(len(rdns))]


def ensure(db: Session, managed_element_ref: str, dn: str, source: str) -> None:
    """Make `dn` and every ancestor exist (an existing row is left as it is)."""
    now = datetime.datetime.now(datetime.UTC)
    parent = None
    for path in ancestors(dn):
        existing = db.get(ManagedObject, path)
        if existing is not None and source == "registry" and existing.source != "registry":
            existing.source = "registry"                       # something the registry vouches for is not swept away by a later walk
        if existing is None:
            cls, ident = parse_ldn(path)[-1]
            db.add(ManagedObject(dn=path, parent_dn=parent, object_class=cls, object_id=ident, managed_element_ref=managed_element_ref,
                                 source=source, updated_at=now))
            db.flush()
        parent = path


def sync_registry(db: Session, me) -> None:
    """Registering an element puts its root, and the function it was registered with, in the tree."""
    ensure(db, me.managed_element_ref, root_dn(me.managed_element_ref), "registry")
    if me.managed_function_ref:
        ensure(db, me.managed_element_ref, target_dn(me.managed_element_ref, me.managed_function_ref), "registry")


def view(obj: ManagedObject) -> dict:
    return {"dn": obj.dn, "parentDn": obj.parent_dn, "class": obj.object_class, "id": obj.object_id,
            "managedElementRef": obj.managed_element_ref, "source": obj.source}


def children_stmt(dn: str):
    return select(ManagedObject).where(ManagedObject.parent_dn == dn).order_by(ManagedObject.object_class, ManagedObject.object_id)


def subtree(db: Session, dn: str, depth: int) -> tuple[dict, bool]:
    """(the object and its descendants down to `depth` levels as nested `children`, whether `MAX_SUBTREE_NODES` cut it short)."""
    root = db.get(ManagedObject, dn)
    budget = [MAX_SUBTREE_NODES]
    truncated = [False]

    def build(obj: ManagedObject, level: int) -> dict:
        node = view(obj)
        budget[0] -= 1
        if level >= depth:
            return node
        kids = []
        for child in db.scalars(children_stmt(obj.dn)).all():
            if budget[0] <= 0:
                truncated[0] = True
                break
            kids.append(build(child, level + 1))
        node["children"] = kids
        return node

    return build(root, 0), truncated[0]


def exists(db: Session, dn: str) -> bool:
    return db.get(ManagedObject, dn) is not None


def apply_walk(db: Session, managed_element_ref: str, relative_paths: list[str]) -> dict:
    """PR-SB-6.2: make the element's walked objects match a server's report. Objects the server reports are added (an existing one is left as it
    is, so a registry row stays a registry row); objects of `source=walk` it no longer reports are removed with whatever hangs below them
    (registry rows are never removed, and their ancestors were promoted to registry when they were created)."""
    root = root_dn(managed_element_ref)
    wanted = {f"{root},{path}" for path in relative_paths}
    wanted_with_ancestors = {a for dn in wanted for a in ancestors(dn)}
    existing = {o.dn: o for o in db.scalars(select(ManagedObject).where(ManagedObject.managed_element_ref == managed_element_ref)).all()}
    removed = [dn for dn, o in existing.items() if o.source == "walk" and dn not in wanted_with_ancestors]
    for dn in sorted(removed, key=len, reverse=True):          # leaves first, so no foreign key is ever violated on the way
        obj = db.get(ManagedObject, dn)
        if obj is not None:
            db.delete(obj)
    db.flush()
    before = set(existing) - set(removed)
    for dn in sorted(wanted):
        ensure(db, managed_element_ref, dn, "walk")
    after = {o.dn for o in db.scalars(select(ManagedObject).where(ManagedObject.managed_element_ref == managed_element_ref)).all()}
    return {"added": len(after - before), "removed": len(removed), "unchanged": len(before & after), "total": len(after)}
