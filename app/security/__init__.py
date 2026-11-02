# app/security/__init__.py
"""Security and Governance Organs (FDE Pillar 2: GOVERN).
Includes Layer-7 Policy Enforcement Points (PEP), Model Armor, Cloud DLP,
and SPIFFE / Workload Identity Agent Gateway enforcement.
"""
from .model_armor import ModelArmor, SecurityViolationError
from .dlp import FinancialDLPPipeline
from .auth import (
    AgentIdentity,
    AgentIdentityManager,
    AgentPermissionError,
    HumanApprovalRequiredError,
    DEFAULT_AGENT_IDENTITIES,
)

__all__ = [
    "ModelArmor",
    "SecurityViolationError",
    "FinancialDLPPipeline",
    "AgentIdentity",
    "AgentIdentityManager",
    "AgentPermissionError",
    "HumanApprovalRequiredError",
    "DEFAULT_AGENT_IDENTITIES",
]
