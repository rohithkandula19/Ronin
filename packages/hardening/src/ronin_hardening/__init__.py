"""Production hardening for Claude agents.

Modules:
- ``injection``       — prompt-injection scanners (pattern + LLM) for input and output.
- ``guardrails``      — tool allowlists and human-in-the-loop approval gates.
- ``validation``      — structured-output validation with retry-on-failure.
- ``tracing``         — provider-agnostic trace emitter + PII redaction.
- ``secret_scanner``  — detect leaked API keys / JWTs / etc. in agent output.
- ``token_budget``    — hard token / cost cap that aborts the run before bills explode.
"""
from .guardrails import ApprovalGate, ToolAllowlist
from .injection import InjectionScanner, OutputLeakScanner, ScanResult
from .secret_scanner import (
    SecretFinding,
    SecretLeakDetected,
    SecretLeakScanner,
    SecretScanResult,
)
from .token_budget import BudgetExceededError, TokenBudget, estimate_cost_usd
from .tracing import TraceEmitter, redact_pii
from .validation import OutputValidator, ValidationFailure

__all__ = [
    "ApprovalGate",
    "BudgetExceededError",
    "InjectionScanner",
    "OutputLeakScanner",
    "OutputValidator",
    "ScanResult",
    "SecretFinding",
    "SecretLeakDetected",
    "SecretLeakScanner",
    "SecretScanResult",
    "TokenBudget",
    "ToolAllowlist",
    "TraceEmitter",
    "ValidationFailure",
    "estimate_cost_usd",
    "redact_pii",
]
