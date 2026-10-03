"""`managed_object` (PR-SB-6.1, 6.6): the managed-object containment tree of RAN NF OAM.

A new table, so the previous release (which does not know it) is unaffected. One row per managed object, keyed by its DN, with its parent (the
same table: a tree), its class and id (the last RDN), the element it belongs to (rows go with the element) and where it came from.

The upgrade also puts what the registry already knows into the tree (SB-6.6): the root `ManagedElement=<ref>` of every registered element
(a flat key; one that is itself a DN is its own root) and, for an element registered with a managed function, that function's DN below the root,
with its ancestors. `ran-nf-oam/app/mo_tree.py` is the model of how those DNs are formed, and `models.py` (ManagedObject) the table's.

Revision ID: 0010
Revises: 0009
"""
import datetime

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _rdns(dn: str) -> list[str]:
    return [part.strip() for part in dn.split(",") if part.strip()]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE managed_object (
          dn                   TEXT PRIMARY KEY,
          parent_dn            TEXT REFERENCES managed_object (dn) ON DELETE CASCADE,
          object_class         TEXT NOT NULL,
          object_id            TEXT NOT NULL,
          managed_element_ref  TEXT NOT NULL REFERENCES managed_entity (managed_element_ref) ON DELETE CASCADE,
          source               TEXT NOT NULL CHECK (source IN ('registry', 'walk')),
          updated_at           TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_managed_object_parent_dn ON managed_object (parent_dn)")
    op.execute("CREATE INDEX ix_managed_object_managed_element_ref ON managed_object (managed_element_ref)")
    bind = op.get_bind()
    now = datetime.datetime.now(datetime.UTC)
    insert = sa.text("INSERT INTO managed_object (dn, parent_dn, object_class, object_id, managed_element_ref, source, updated_at) "
                     "VALUES (:dn, :parent, :cls, :ident, :element, 'registry', :now) ON CONFLICT (dn) DO NOTHING")
    for element, function in bind.execute(sa.text("SELECT managed_element_ref, managed_function_ref FROM managed_entity")).fetchall():
        root = element if "=" in element else f"ManagedElement={element}"
        target = root
        if function and "=" in function:
            target = function if function.startswith("ManagedElement=") else f"{root},{function}"
        rdns = _rdns(target)
        for n in range(len(rdns)):
            cls, _, ident = rdns[n].partition("=")
            if not cls or not ident:
                break                                                    # a malformed DN already in the registry: keep what came before it
            bind.execute(insert, {"dn": ",".join(rdns[: n + 1]), "parent": ",".join(rdns[:n]) or None, "cls": cls, "ident": ident,
                                  "element": element, "now": now})


def downgrade() -> None:
    op.execute("DROP TABLE managed_object")
