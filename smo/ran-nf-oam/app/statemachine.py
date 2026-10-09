"""Separate lifecycles hosted by RAN NF OAM SMOS, the first three per RAN NF OAM LLD
section 6:
  - WriteConfigJob      (section 3.2's decomposed-PATCH aggregation)
  - SoftwareManagementJob
  - O1AdaptorEndpoint health (section 1.2's new endpoint registry)
  - the onboarding of a newly registered element (MGT-14.5)
  - a software campaign over many elements (MGT-15)

Kept as independent StateMachine instances rather than one shared
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
    HALTED = "HALTED"                   # MGT-5: a staged job waiting between waves (a pause, a failed gate, an operator's halt)
    COMPLETED = "COMPLETED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"


class JobEvent(StrEnum):
    PRECHECK_PASS = "PRECHECK_PASS"     # noqa: S105 — an event name, not a credential; schema validation + MSAC gate both pass
    PRECHECK_FAIL = "PRECHECK_FAIL"
    AGGREGATE_ALL_APPLIED = "AGGREGATE_ALL_APPLIED"
    AGGREGATE_ALL_REJECTED = "AGGREGATE_ALL_REJECTED"
    AGGREGATE_MIXED = "AGGREGATE_MIXED"
    HALT = "HALT"                       # MGT-5.3/5.4: stop between waves
    RESUME = "RESUME"                   # MGT-5.4: go on with the next wave


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
    # MGT-5: a staged job stops between waves and either goes on or ends with what it has (an abort, or the end of an automatic revert)
    fsm.add(JobState.PROCESSING, JobEvent.HALT, JobState.HALTED)
    fsm.add(JobState.HALTED, JobEvent.RESUME, JobState.PROCESSING)
    fsm.add(JobState.HALTED, JobEvent.AGGREGATE_ALL_APPLIED, JobState.COMPLETED)
    fsm.add(JobState.HALTED, JobEvent.AGGREGATE_ALL_REJECTED, JobState.FAILED)
    fsm.add(JobState.HALTED, JobEvent.AGGREGATE_MIXED, JobState.PARTIAL_SUCCESS)
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

# ---------------------------------------------------------------- Onboarding of an element (MGT-14.5)

class OnboardingState(StrEnum):
    DISCOVERED = "DISCOVERED"               # registered, not yet matched against the templates
    NO_TEMPLATE = "NO_TEMPLATE"             # templates exist, none fits this element
    TEMPLATE_SELECTED = "TEMPLATE_SELECTED"
    APPLYING = "APPLYING"                   # the template's config job is being written
    ONBOARDED = "ONBOARDED"
    FAILED = "FAILED"


class OnboardingEvent(StrEnum):
    TEMPLATE_MATCHED = "TEMPLATE_MATCHED"
    NO_MATCH = "NO_MATCH"
    APPLY = "APPLY"
    APPLIED = "APPLIED"
    APPLY_FAILED = "APPLY_FAILED"


def build_onboarding_fsm() -> StateMachine[OnboardingState, OnboardingEvent]:
    """The way an element goes from "registered" to "configured". Selecting again (an operator changed the templates) is allowed wherever the element is not
    being written; applying again is allowed from ONBOARDED, FAILED and TEMPLATE_SELECTED."""
    fsm: StateMachine[OnboardingState, OnboardingEvent] = StateMachine()
    S, E = OnboardingState, OnboardingEvent
    for state in (S.DISCOVERED, S.NO_TEMPLATE, S.TEMPLATE_SELECTED, S.ONBOARDED, S.FAILED):
        fsm.add(state, E.TEMPLATE_MATCHED, S.TEMPLATE_SELECTED)
    for state in (S.DISCOVERED, S.NO_TEMPLATE, S.TEMPLATE_SELECTED, S.ONBOARDED, S.FAILED):
        fsm.add(state, E.NO_MATCH, S.NO_TEMPLATE)
    for state in (S.TEMPLATE_SELECTED, S.ONBOARDED, S.FAILED):
        fsm.add(state, E.APPLY, S.APPLYING)
    fsm.add(S.APPLYING, E.APPLIED, S.ONBOARDED)
    fsm.add(S.APPLYING, E.APPLY_FAILED, S.FAILED)
    return fsm


ONBOARDING_FSM = build_onboarding_fsm()

# ---------------------------------------------------------------- Software campaign (MGT-15)

class CampaignState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"                     # a wave is in progress
    HALTED = "HALTED"                       # between waves: a pause, a failed gate, an operator's halt
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"                     # ended by an operator before the last wave
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


class CampaignEvent(StrEnum):
    START = "START"
    HALT = "HALT"
    RESUME = "RESUME"
    FINISH = "FINISH"
    ABORT = "ABORT"
    ROLLBACK = "ROLLBACK"
    ROLLBACK_DONE = "ROLLBACK_DONE"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


def build_campaign_fsm() -> StateMachine[CampaignState, CampaignEvent]:
    fsm: StateMachine[CampaignState, CampaignEvent] = StateMachine()
    S, E = CampaignState, CampaignEvent
    fsm.add(S.PENDING, E.START, S.RUNNING)
    fsm.add(S.RUNNING, E.HALT, S.HALTED)
    fsm.add(S.RUNNING, E.FINISH, S.COMPLETED)
    fsm.add(S.HALTED, E.RESUME, S.RUNNING)
    fsm.add(S.HALTED, E.ABORT, S.ABORTED)
    for state in (S.RUNNING, S.HALTED, S.COMPLETED, S.ABORTED, S.ROLLBACK_FAILED):
        fsm.add(state, E.ROLLBACK, S.ROLLING_BACK)
    fsm.add(S.ROLLING_BACK, E.ROLLBACK_DONE, S.ROLLED_BACK)
    fsm.add(S.ROLLING_BACK, E.ROLLBACK_FAILED, S.ROLLBACK_FAILED)
    return fsm


CAMPAIGN_FSM = build_campaign_fsm()
