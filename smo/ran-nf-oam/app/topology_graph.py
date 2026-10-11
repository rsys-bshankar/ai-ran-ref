"""The containment tree as a graph for the console (PR-GUI-3, GUI-3.1 and the alarm overlay of GUI-3.3): `GET /topology/graph`.

The nodes are the managed objects (`managed_object`, PR-SB-6) of the elements a caller may read, in DN order, at most `max_nodes` of them; the
edges are the parent links between two nodes that are both in the answer. Each node carries the open alarms raised on it: an alarm is placed on
the node its `managed_function_ref` names below its element (`mo_tree.target_dn`, the rule the write routes use), or on the element's root when
that node is not in the tree (an alarm the tree does not model still shows on its element). Cleared alarms are not counted. The overlay is one
grouped query over the alarms of the elements in the answer, so the graph costs two reads whatever its size.

`GET /topology` is the export of the same tree in TEIV's wire shape; this route is the console's view of it, smaller and with the alarms joined.
"""

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from . import mo_tree
from .alarm_query import GRADED_SEVERITIES, SEVERITY_RANK
from .models import Alarm, ManagedObject
from .scoping import narrowed_to_place

MAX_GRAPH_NODES = 2000
DEFAULT_GRAPH_NODES = 500
OVERLAY_SEVERITIES = (*GRADED_SEVERITIES, "indeterminate")


def _place(stmt: Select, element: str | None, region: str | None, site_cluster: str | None) -> Select:
    """`stmt` over managed objects narrowed to one element and to the elements of a place (region and site cluster); unchanged without them."""
    if element:
        stmt = stmt.where(ManagedObject.managed_element_ref == element)
    return narrowed_to_place(stmt, ManagedObject.managed_element_ref, region, site_cluster)


def graph(db: Session, restrict: Callable[[Select], Select], element: str | None = None, region: str | None = None,
          site_cluster: str | None = None, max_nodes: int = DEFAULT_GRAPH_NODES) -> dict:
    """`{nodes, edges, total, truncated}`: the nodes (`mo_tree.view` plus `alarms`, a count per open severity, and `worst`, the most severe or
    null), the parent links among them (`{child, parent}`), how many nodes there are in all and whether `max_nodes` cut the answer short.
    `restrict` limits the nodes to the caller's (main.py `_tree_filter`: the scope claim and MSAC)."""
    base = _place(restrict(select(ManagedObject)), element, region, site_cluster)
    total = db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    objects = db.scalars(base.order_by(ManagedObject.dn).limit(max_nodes)).all()
    nodes = {o.dn: {**mo_tree.view(o), "alarms": {}, "worst": None} for o in objects}
    elements = {o.managed_element_ref for o in objects}
    if elements:
        rows = db.execute(select(Alarm.managed_element_ref, Alarm.managed_function_ref, Alarm.severity, func.count())
                          .where(Alarm.managed_element_ref.in_(elements), Alarm.severity != "cleared")
                          .group_by(Alarm.managed_element_ref, Alarm.managed_function_ref, Alarm.severity))
        for element_ref, function_ref, severity, count in rows:
            node = nodes.get(mo_tree.target_dn(element_ref, function_ref)) or nodes.get(mo_tree.root_dn(element_ref))
            if node is None:                                         # the element's root is past the cut: the alarm is not shown on this page
                continue
            node["alarms"][severity] = node["alarms"].get(severity, 0) + count
    for node in nodes.values():
        if node["alarms"]:
            node["worst"] = min(node["alarms"], key=lambda s: SEVERITY_RANK.get(s, len(SEVERITY_RANK)))
    edges = [{"child": dn, "parent": n["parentDn"]} for dn, n in nodes.items() if n["parentDn"] in nodes]
    return {"nodes": list(nodes.values()), "edges": edges, "total": total, "truncated": total > len(nodes)}
