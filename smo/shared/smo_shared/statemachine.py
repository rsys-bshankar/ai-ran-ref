"""Minimal finite-state-machine base every module's lifecycle implementation
extends. Deliberately small: this SMO's state machines (WriteConfigJob,
SoftwareManagementJob, O1AdaptorEndpoint health, ApplicationPackage,
RAppInstance, MLModel, InferenceJob) are all simple enough that a full FSM
framework would be more machinery than the problem needs — each one is just
a set of (from_state, event) -> to_state transitions plus optional guards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

S = TypeVar("S")  # state enum/str type
E = TypeVar("E")  # event enum/str type


class IllegalTransition(Exception):
    def __init__(self, state: S, event: E):
        super().__init__(f"no transition for event {event!r} in state {state!r}")
        self.state = state
        self.event = event


@dataclass
class Transition(Generic[S, E]):
    from_state: S
    event: E
    to_state: S
    guard: Callable[..., bool] | None = None  # optional precondition, e.g. schema-checked
    action: Callable[..., None] | None = None  # optional side effect, e.g. publish DME event


@dataclass
class StateMachine(Generic[S, E]):
    """Instantiate once per model class (e.g. WriteConfigJobFSM), reuse across
    every instance of that model — this holds the transition TABLE, not any
    one object's current state.
    """

    transitions: list[Transition[S, E]] = field(default_factory=list)

    def add(self, from_state: S, event: E, to_state: S, guard=None, action=None) -> "StateMachine":
        self.transitions.append(Transition(from_state, event, to_state, guard, action))
        return self

    def fire(self, current_state: S, event: E, **context) -> S:
        """Evaluate every matching transition's guard in order; the first
        whose guard passes (or has none) wins. Raises IllegalTransition if
        no transition matches at all, or every guard rejects.
        """
        candidates = [t for t in self.transitions if t.from_state == current_state and t.event == event]
        if not candidates:
            raise IllegalTransition(current_state, event)
        for t in candidates:
            if t.guard is None or t.guard(**context):
                if t.action is not None:
                    t.action(**context)
                return t.to_state
        raise IllegalTransition(current_state, event)

    def legal_events(self, current_state: S) -> list[E]:
        return [t.event for t in self.transitions if t.from_state == current_state]
