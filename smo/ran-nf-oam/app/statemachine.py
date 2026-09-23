"""Three separate lifecycles hosted by RAN NF OAM SMOS, per RAN NF OAM LLD
section 6:
  - WriteConfigJob      (section 3.2's decomposed-PATCH aggregation)
  - SoftwareManagementJob
  - O1AdaptorEndpoint health (section 1.2's new endpoint registry)

Kept as three independent StateMachine instances rather than one shared
FSM — the entities don't share transitions or a common lifecycle shape,
so merging them would just be false economy.
"""

from __future__ import annotations

from enum import StrEnum

from smo_shared.statemachine import StateMachine

# ---------------------------------------------------------------- WriteConfigJob

class JobState(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"


class JobEvent(StrEnum):
    PRECHECK_PASS = "PRECHECK_PASS"     # schema validation + MSAC gate both pass
    PRECHECK_FAIL = "PRECHECK_FAIL"
    AGGREGATE_ALL_APPLIED = "AGGREGATE_ALL_APPLIED"
    AGGREGATE_ALL_REJECTED = "AGGREGATE_ALL_REJECTED"
    AGGREGATE_MIXED = "AGGREGATE_MIXED"


def aggregate_event(sub_change_statuses: list[str]) -> JobEvent:
    """RAN NF OAM LLD section 3.2: aggregate over the decomposed per-attribute
    PATCH outcomes. Each underlying PATCH stays atomic (TS 28.532's own
    all-or-nothing semantics, never violated); PARTIAL_SUCCESS is a
    framework-level aggregation over multiple atomic calls, computed here.
    """
    applied = sum(1 for s in sub_change_statuses if s == "APPLIED")
    rejected = sum(1 for s in sub_change_statuses if s == "REJECTED")
    if applied and rejected:
        return JobEvent.AGGREGATE_MIXED
    if applied and not rejected:
        return JobEvent.AGGREGATE_ALL_APPLIED
    return JobEvent.AGGREGATE_ALL_REJECTED


def build_write_config_job_fsm() -> StateMachine[JobState, JobEvent]:
    fsm: StateMachine[JobState, JobEvent] = StateMachine()
    fsm.add(JobState.PENDING, JobEvent.PRECHECK_PASS, JobState.PROCESSING)
    fsm.add(JobState.PENDING, JobEvent.PRECHECK_FAIL, JobState.FAILED)
    fsm.add(JobState.PROCESSING, JobEvent.AGGREGATE_ALL_APPLIED, JobState.COMPLETED)
    fsm.add(JobState.PROCESSING, JobEvent.AGGREGATE_ALL_REJECTED, JobState.FAILED)
    fsm.add(JobState.PROCESSING, JobEvent.AGGREGATE_MIXED, JobState.PARTIAL_SUCCESS)
    return fsm


WRITE_CONFIG_JOB_FSM = build_write_config_job_fsm()

# ---------------------------------------------------------------- SoftwareManagementJob

class SwmState(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SwmPhase(StrEnum):
    DOWNLOAD = "DOWNLOAD"
    INSTALL = "INSTALL"
    ACTIVATE = "ACTIVATE"


class SwmEvent(StrEnum):
    START = "START"
    DOWNLOAD_OK = "DOWNLOAD_OK"
    INSTALL_OK = "INSTALL_OK"
    ACTIVATE_OK = "ACTIVATE_OK"
    PHASE_FAILED = "PHASE_FAILED"


def build_software_management_fsm() -> StateMachine[SwmState, SwmEvent]:
    """Status transitions only — phase is tracked as separate data on the
    job row (advanced alongside DOWNLOAD_OK/INSTALL_OK), since v1.3's
    SoftwareManagementJob models phase and status as two independent
    fields, not one combined state.
    """
    fsm: StateMachine[SwmState, SwmEvent] = StateMachine()
    fsm.add(SwmState.PENDING, SwmEvent.START, SwmState.IN_PROGRESS)
    fsm.add(SwmState.IN_PROGRESS, SwmEvent.DOWNLOAD_OK, SwmState.IN_PROGRESS)
    fsm.add(SwmState.IN_PROGRESS, SwmEvent.INSTALL_OK, SwmState.IN_PROGRESS)
    fsm.add(SwmState.IN_PROGRESS, SwmEvent.ACTIVATE_OK, SwmState.COMPLETED)
    fsm.add(SwmState.IN_PROGRESS, SwmEvent.PHASE_FAILED, SwmState.FAILED)
    return fsm


SOFTWARE_MANAGEMENT_FSM = build_software_management_fsm()

PHASE_ORDER = {SwmEvent.DOWNLOAD_OK: SwmPhase.INSTALL, SwmEvent.INSTALL_OK: SwmPhase.ACTIVATE}

# ---------------------------------------------------------------- O1AdaptorEndpoint health

class EndpointHealth(StrEnum):
    DISCOVERED = "DISCOVERED"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"


class EndpointEvent(StrEnum):
    HEARTBEAT = "HEARTBEAT"
    MISSED_HEARTBEATS = "MISSED_HEARTBEATS"
    DEREGISTERED = "DEREGISTERED"
    RE_REGISTERED = "RE_REGISTERED"


def build_endpoint_health_fsm() -> StateMachine[EndpointHealth, EndpointEvent]:
    """RAN NF OAM LLD section 6 — the endpoint registry's own health
    lifecycle, new in this LLD pass (there was no endpoint registry
    concept in v1.3's single-endpoint-assumption design).
    """
    fsm: StateMachine[EndpointHealth, EndpointEvent] = StateMachine()
    fsm.add(EndpointHealth.DISCOVERED, EndpointEvent.HEARTBEAT, EndpointHealth.ACTIVE)
    fsm.add(EndpointHealth.ACTIVE, EndpointEvent.MISSED_HEARTBEATS, EndpointHealth.DEGRADED)
    fsm.add(EndpointHealth.DEGRADED, EndpointEvent.HEARTBEAT, EndpointHealth.ACTIVE)
    fsm.add(EndpointHealth.DEGRADED, EndpointEvent.DEREGISTERED, EndpointHealth.UNREACHABLE)
    fsm.add(EndpointHealth.UNREACHABLE, EndpointEvent.RE_REGISTERED, EndpointHealth.DISCOVERED)
    return fsm


ENDPOINT_HEALTH_FSM = build_endpoint_health_fsm()
