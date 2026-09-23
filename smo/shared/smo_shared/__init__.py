"""Shared library for the AI-RAN SMO Phase 1 reference implementation.

Every one of the fourteen SMOS modules depends on this package for:
  - db: SQLAlchemy engine/session against the shared Postgres instance
        (moduleScope partition, per Requirements v0.1 section 3 cross-cutting decision)
  - identity: the rAppId <-> RAppInstance.instanceId equivalence
        (Foundational Platform LLD section 1)
  - errors: RFC 7807 ProblemDetails, the error model every R1 service
        group defers to except A1 policy management (A1 Related LLD section 1.3)
  - statemachine: a minimal FSM base every module's lifecycle implementation extends
"""
