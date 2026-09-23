"""ApplicationPackage lifecycle.

SMO Design v1.3 section 3.4 state diagram (AVAILABLE <-> DEPRECATED -> deleted)
extended by Onboarding/rApp Mgmt LLD section 3 (the FAILED terminal state
v1.3 never modeled) and section 4 (the cascade-delete guard, concretized
here as an actual guard function rather than just a stated rule).
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.statemachine import StateMachine

from .models import ApplicationPackage, PackageUsageRegistration


class PackageState(StrEnum):
    ONBOARDING = "ONBOARDING"
    AVAILABLE = "AVAILABLE"
    DEPRECATED = "DEPRECATED"
    DELETING = "DELETING"
    FAILED = "FAILED"


class PackageEvent(StrEnum):
    VALIDATE_OK = "VALIDATE_OK"
    VALIDATE_FAILED = "VALIDATE_FAILED"
    DEPRECATE = "DEPRECATE"
    CANCEL_DELETE = "CANCEL_DELETE"
    DELETE = "DELETE"


def _no_blocking_dependents(db: Session, package: ApplicationPackage) -> bool:
    """Onboarding/rApp Mgmt LLD section 4 — the actual cascade-delete query,
    not just the stated rule: blocked if any child package is
    AVAILABLE/DEPRECATED, or any usage registration has no stopped_at.
    """
    blocking_child = db.scalar(
        select(ApplicationPackage).where(
            ApplicationPackage.parent_package_id == package.package_id,
            ApplicationPackage.state.in_([PackageState.AVAILABLE, PackageState.DEPRECATED]),
        ).limit(1)
    )
    if blocking_child is not None:
        return False
    active_usage = db.scalar(
        select(PackageUsageRegistration).where(
            PackageUsageRegistration.package_id == package.package_id,
            PackageUsageRegistration.stopped_at.is_(None),
        ).limit(1)
    )
    return active_usage is None


def build_onboarding_fsm() -> StateMachine[PackageState, PackageEvent]:
    fsm: StateMachine[PackageState, PackageEvent] = StateMachine()
    fsm.add(PackageState.ONBOARDING, PackageEvent.VALIDATE_OK, PackageState.AVAILABLE)
    fsm.add(PackageState.ONBOARDING, PackageEvent.VALIDATE_FAILED, PackageState.FAILED)
    fsm.add(PackageState.AVAILABLE, PackageEvent.DEPRECATE, PackageState.DEPRECATED)
    fsm.add(PackageState.DEPRECATED, PackageEvent.CANCEL_DELETE, PackageState.AVAILABLE)
    fsm.add(
        PackageState.DEPRECATED,
        PackageEvent.DELETE,
        PackageState.DELETING,
        guard=lambda db, package, **_: _no_blocking_dependents(db, package),
    )
    fsm.add(
        PackageState.AVAILABLE,
        PackageEvent.DELETE,
        PackageState.DELETING,
        guard=lambda db, package, **_: _no_blocking_dependents(db, package),
    )
    # FAILED is terminal: nothing could depend on a package that never reached
    # AVAILABLE, so DeletePackage from FAILED skips the cascade check entirely
    # (Onboarding/rApp Mgmt LLD section 3) and is handled as a direct delete
    # by the caller, not through this FSM.
    return fsm


ONBOARDING_FSM = build_onboarding_fsm()
