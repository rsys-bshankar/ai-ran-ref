"""PR-MGT-10.2: what kind of link joins two things in the topology this service holds.

Two kinds of fact are held: the containment tree of managed objects (`mo_tree.py`, exported as TEIV `MANAGEDOBJECT_CHILD_OF_MANAGEDOBJECT`) and the
neighbour relations an operator declared per cell (`neighbourRefs` in the cell guards of the registry). Root-cause reasoning needs to know which of
them joins two alarming things: a parent and its child (an alarm on the child is probably an effect), two cells of one element (one box, one fault
domain), or two cells of different elements (a fault may spread by the radio relation, not through the box).

The link types are this build's own vocabulary, not the TEIV RAN domain model: nothing here claims which 3GPP interface (Xn, F1) carries a relation,
because the data does not say.

Cell links (a neighbour ref is a cell id; it is resolved by looking for the element that owns that cell id in its cell guards):
  INTRA_ELEMENT  both cells belong to one managed element
  INTER_ELEMENT  the cells belong to different managed elements
  AMBIGUOUS      several elements declare a cell with that id: not guessed
  EXTERNAL       no managed element declares it (a cell outside what this service manages)

Containment relations between two DNs (`a` relative to `b`): SAME, ANCESTOR (a contains b), DESCENDANT, SIBLING (same parent), SAME_ELEMENT (different
branches of one element) and DIFFERENT_ELEMENT.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mo_tree
from .models import ManagedEntity, ManagedObject

CELL_LINK_TYPES = ("INTRA_ELEMENT", "INTER_ELEMENT", "AMBIGUOUS", "EXTERNAL")


def cell_links(db: Session, managed_element_ref: str | None = None, link_type: str | None = None) -> list[dict]:
    """Every declared neighbour relation, as declared (a lists b), with its type and whether b lists a back. `managed_element_ref` keeps the links
    with that element at either end; `link_type` keeps one type."""
    entities = db.scalars(select(ManagedEntity).order_by(ManagedEntity.managed_element_ref)).all()
    guards = {e.managed_element_ref: (e.cell_guards or {}) for e in entities}
    owners: dict[str, list[str]] = {}
    for element, cells in guards.items():
        for cell_id in cells:
            owners.setdefault(cell_id, []).append(element)

    links = []
    for element, cells in guards.items():
        for cell_id, guard in sorted(cells.items()):
            for ref in guard.get("neighbourRefs") or []:
                if ref == cell_id and element in owners.get(ref, []):
                    continue                                                            # a cell is not its own neighbour
                found = owners.get(ref, [])
                if not found:
                    kind, other = "EXTERNAL", None
                elif len(found) > 1:
                    kind, other = "AMBIGUOUS", None
                else:
                    other = found[0]
                    kind = "INTRA_ELEMENT" if other == element else "INTER_ELEMENT"
                other_guard = guards.get(other, {}).get(ref, {}) if other else {}
                back = other_guard.get("neighbourRefs") or []
                links.append({
                    "aElement": element, "aCell": cell_id, "bElement": other, "bCell": ref, "linkType": kind,
                    "reciprocal": cell_id in back,
                    "sameSectorGroup": bool(guard.get("sectorGroup")) and guard.get("sectorGroup") == other_guard.get("sectorGroup"),
                    "sameIncidentZone": bool(guard.get("incidentZone")) and guard.get("incidentZone") == other_guard.get("incidentZone"),
                })
    if managed_element_ref:
        links = [link for link in links if managed_element_ref in (link["aElement"], link["bElement"])]
    if link_type:
        links = [link for link in links if link["linkType"] == link_type]
    return links


def containment_relation(db: Session, a: ManagedObject, b: ManagedObject) -> str:
    """How `a` stands to `b` in the containment tree."""
    if a.dn == b.dn:
        return "SAME"
    if a.managed_element_ref != b.managed_element_ref:
        return "DIFFERENT_ELEMENT"
    chain_a, chain_b = mo_tree.ancestors(a.dn), mo_tree.ancestors(b.dn)
    if a.dn in chain_b:
        return "ANCESTOR"
    if b.dn in chain_a:
        return "DESCENDANT"
    if a.parent_dn is not None and a.parent_dn == b.parent_dn:
        return "SIBLING"
    return "SAME_ELEMENT"
