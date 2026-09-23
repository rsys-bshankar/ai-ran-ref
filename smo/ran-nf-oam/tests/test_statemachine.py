"""Tests for RAN NF OAM's three lifecycles (RAN NF OAM LLD section 6).
Run with: pytest smo/ran-nf-oam/tests -q
"""

import pytest

from smo_shared.statemachine import IllegalTransition

from app.statemachine import (
    ENDPOINT_HEALTH_FSM,
    SOFTWARE_MANAGEMENT_FSM,
    WRITE_CONFIG_JOB_FSM,
    EndpointEvent,
    EndpointHealth,
    JobEvent,
    JobState,
    SwmEvent,
    SwmState,
    aggregate_event,
)


# ------------------------------------------------------------ WriteConfigJob

def test_write_config_job_all_applied_completes():
    event = aggregate_event(["APPLIED", "APPLIED", "APPLIED"])
    assert event == JobEvent.AGGREGATE_ALL_APPLIED
    assert WRITE_CONFIG_JOB_FSM.fire(JobState.PROCESSING, event) == JobState.COMPLETED


def test_write_config_job_all_rejected_fails():
    event = aggregate_event(["REJECTED", "REJECTED"])
    assert event == JobEvent.AGGREGATE_ALL_REJECTED
    assert WRITE_CONFIG_JOB_FSM.fire(JobState.PROCESSING, event) == JobState.FAILED


def test_write_config_job_mixed_is_partial_success():
    """The core finding of RAN NF OAM LLD section 3.2: TS 28.532's PATCH is
    all-or-nothing, but the framework's own aggregation over N decomposed
    atomic PATCH calls can and does produce PARTIAL_SUCCESS.
    """
    event = aggregate_event(["APPLIED", "REJECTED", "APPLIED"])
    assert event == JobEvent.AGGREGATE_MIXED
    assert WRITE_CONFIG_JOB_FSM.fire(JobState.PROCESSING, event) == JobState.PARTIAL_SUCCESS


def test_write_config_job_precheck_failure_never_reaches_processing():
    new_state = WRITE_CONFIG_JOB_FSM.fire(JobState.PENDING, JobEvent.PRECHECK_FAIL)
    assert new_state == JobState.FAILED
    with pytest.raises(IllegalTransition):
        WRITE_CONFIG_JOB_FSM.fire(new_state, JobEvent.AGGREGATE_ALL_APPLIED)


# ------------------------------------------------------------ SoftwareManagementJob

def test_software_management_full_sequence():
    s = SOFTWARE_MANAGEMENT_FSM.fire(SwmState.PENDING, SwmEvent.START)
    assert s == SwmState.IN_PROGRESS
    s = SOFTWARE_MANAGEMENT_FSM.fire(s, SwmEvent.DOWNLOAD_OK)
    s = SOFTWARE_MANAGEMENT_FSM.fire(s, SwmEvent.INSTALL_OK)
    s = SOFTWARE_MANAGEMENT_FSM.fire(s, SwmEvent.ACTIVATE_OK)
    assert s == SwmState.COMPLETED


def test_software_management_phase_failure():
    s = SOFTWARE_MANAGEMENT_FSM.fire(SwmState.PENDING, SwmEvent.START)
    s = SOFTWARE_MANAGEMENT_FSM.fire(s, SwmEvent.PHASE_FAILED)
    assert s == SwmState.FAILED


# ------------------------------------------------------------ O1AdaptorEndpoint health

def test_endpoint_discovered_to_active_on_first_heartbeat():
    s = ENDPOINT_HEALTH_FSM.fire(EndpointHealth.DISCOVERED, EndpointEvent.HEARTBEAT)
    assert s == EndpointHealth.ACTIVE


def test_endpoint_degrades_then_recovers():
    s = ENDPOINT_HEALTH_FSM.fire(EndpointHealth.ACTIVE, EndpointEvent.MISSED_HEARTBEATS)
    assert s == EndpointHealth.DEGRADED
    s = ENDPOINT_HEALTH_FSM.fire(s, EndpointEvent.HEARTBEAT)
    assert s == EndpointHealth.ACTIVE


def test_endpoint_degrades_then_deregisters_then_rediscovered():
    """RAN NF OAM LLD section 1.2/6: any managed_entity FK-referencing an
    UNREACHABLE endpoint fails writes/reads until re-registration — this
    test only covers the endpoint's own health lifecycle; the
    ENDPOINT_UNREACHABLE error path is exercised at the route layer.
    """
    s = ENDPOINT_HEALTH_FSM.fire(EndpointHealth.ACTIVE, EndpointEvent.MISSED_HEARTBEATS)
    s = ENDPOINT_HEALTH_FSM.fire(s, EndpointEvent.DEREGISTERED)
    assert s == EndpointHealth.UNREACHABLE
    s = ENDPOINT_HEALTH_FSM.fire(s, EndpointEvent.RE_REGISTERED)
    assert s == EndpointHealth.DISCOVERED


def test_endpoint_cannot_skip_active_straight_to_unreachable():
    """DEREGISTERED is only legal from DEGRADED, not from ACTIVE directly —
    matches the FSM's own documented path (section 6 of the LLD).
    """
    with pytest.raises(IllegalTransition):
        ENDPOINT_HEALTH_FSM.fire(EndpointHealth.ACTIVE, EndpointEvent.DEREGISTERED)
