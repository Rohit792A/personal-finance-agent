# app/security/__init__.py
"""Security and Governance Organs (FDE Pillar 2: GOVERN).
Includes Layer-7 Policy Enforcement Points (PEP), Model Armor, Cloud DLP,
and SPIFFE / Workload Identity Agent Gateway enforcement.

What is local and what calls Google (DEC-063):
- `ModelArmor`: a local regex screen, plus the Google Cloud Model Armor API (`model_armor_api.py`)
  when MODEL_ARMOR_TEMPLATE is set.
- `FinancialDLPPipeline`: local regex tokens, plus Google Sensitive Data Protection (`sdp_api.py`)
  when SDP_ENABLED=1.
- `AgentIdentityManager`: SPIFFE-style IDs with HMAC-signed workload tokens, a local stand-in for
  SPIFFE / Workload Identity (no SPIRE, no Google Workload Identity Federation).
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
