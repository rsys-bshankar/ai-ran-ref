"""rAppId <-> RAppInstance.instanceId equivalence.

Foundational Platform LLD section 1: R1UCR clause 12.1's "rApp registration" step
(the thing that assigns an rAppId) is satisfied by rApp Management SMOS's
CreateInstance operation, happening SMO-internally before the container starts,
using RAppInstance.oauthClientId as the per-instance credential.

Decision, stated once here so every module imports it rather than re-deriving it:
RAppInstance.instanceId IS the framework's rAppId. Every producerId, consumerId,
api-invoker-id, and apfId value anywhere in SME/DME/A1/RAN-NF-OAM/AI-ML-Workflow
MUST be this exact value — never a freshly invented string.
"""

from uuid import UUID


def rapp_id_from_instance(instance_id: UUID) -> str:
    """The canonical rAppId string derived from an rApp instance's identity."""
    return str(instance_id)


def is_framework_internal_identity(identity: str) -> bool:
    """SO SMOS and SA SMOS register as RMIH producers via SME, but they are
    SMO-internal modules, not rApps (Policy Mgmt & Info LLD section 3) — their
    identity is a service name, never an rApp UUID. Used to distinguish the
    two cases wherever a producerId/rmihId is checked.
    """
    try:
        UUID(identity)
        return False
    except ValueError:
        return True
