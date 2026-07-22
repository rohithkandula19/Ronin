"""Ronin Vault — private memory with explicit scopes and hard boundaries.

Rules this package enforces (not merely documents):

1. Every memory item has an explicit scope, owner, sensitivity, and retention.
2. Nothing is training-eligible by default; eligibility requires an explicit
   consent reference and can never be inferred from use.
3. Industry memory is isolated: healthcare memory never surfaces in coding,
   and cross-world transfer happens only through an explicit, previewed move.
4. Organization memory never surfaces in personal workspaces.
5. Recall is auditable: every use of a memory item is recorded and queryable
   ("why did the model see this?").
"""

from .store import (
    IsolationError,
    MemoryItem,
    MemoryScope,
    Sensitivity,
    TransferPreview,
    VaultStore,
)

__all__ = [
    "IsolationError",
    "MemoryItem",
    "MemoryScope",
    "Sensitivity",
    "TransferPreview",
    "VaultStore",
]
