"""Local Distinguished Names (TS 32.300 DN syntax) for managed-object refs.

SA-RANOAM-4 / SA-O1-1. A managed function or element reference is either a
flat id (``NRCellDU=101`` kept working, and so does ``ME-1``) or a DN: a
comma-separated list of ``Class=id`` RDNs, root first
(``SubNetwork=A,ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101``). A ref that
contains ``=`` must parse as a DN; the IOC class is the last RDN's class.
"""

import re

_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_ldn(ref: str) -> list[tuple[str, str]]:
    """The (class, id) RDNs of `ref`; ValueError if it is not a well-formed DN."""
    rdns = []
    for part in ref.split(","):
        cls, sep, ident = part.partition("=")
        if not sep or not _CLASS.match(cls) or not ident.strip():
            raise ValueError(f"{ref!r} is not a distinguished name: bad RDN {part!r} (expected Class=id)")
        rdns.append((cls, ident.strip()))
    return rdns


def check_ref(ref: str | None) -> str | None:
    """`ref` unchanged; a ref carrying '=' must be a valid DN (ValueError otherwise)."""
    if ref and "=" in ref:
        parse_ldn(ref)
    return ref


def leaf_class(ref: str | None) -> str | None:
    """The IOC class a ref names (its last RDN), or None for a flat id."""
    if not ref or "=" not in ref:
        return None
    return parse_ldn(ref)[-1][0]


def leaf_id(ref: str | None) -> str | None:
    """The id of the ref's last RDN (`NRCellDU=101` gives `101`), the flat ref itself when it has no '=', or None for an empty ref. A ref with '=' that is not a valid DN raises ValueError (via `parse_ldn`).
    """
    if not ref:
        return None
    return parse_ldn(ref)[-1][1] if "=" in ref else ref
