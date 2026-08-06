"""Production-grade agent patterns for Claude — and any OpenAI-compatible LLM.

Five patterns shipped:
- ``ReActAgent`` — Reason-Act-Observe loop with tool retry and iteration cap.
- ``PlannerExecutorAgent`` — Plan-then-execute with checkpoint/resume.
- ``SupervisorAgent`` — Delegates dynamically to specialist sub-agents via tools.
- ``OrchestratorAgent`` — Decomposes a goal into assigned subtasks and runs
  provider-agnostic sub-agents (parallel where independent), then synthesizes.
- ``ReflexionAgent`` — Act → critique → retry-with-feedback.

Plus shared types (``Tool``, ``AgentResult``, ``Step``) and providers
(``AnthropicProvider``, ``OpenAICompatProvider``, ``OllamaProvider``, ``FakeProvider``).
"""
from .effort import (
    EFFORT_LEVELS,
    describe_effort,
    effort_to_params,
    normalize_effort,
)
from .contracts import AgentRequest, ContextAssembly, ContextFragment, ContextProvider, assemble_context
from .durable import BudgetDecision, BudgetExceeded, BudgetLimits, DurableRunError, JournalEvent, RunBudget, RunJournal
from .orchestrator import (
    OrchestrationPlan,
    OrchestrationResult,
    OrchestratorAgent,
    OrchestratorSubAgent,
    Subtask,
    SubtaskResult,
)
from .plan_cache import Plan, PlanCache, repository_fingerprint
from .planner_executor import PlannerExecutorAgent
from .providers import (
    AnthropicProvider,
    FailoverProvider,
    FakeProvider,
    LLMProvider,
    LLMResponse,
    Message,
    OllamaProvider,
    OpenAICompatProvider,
    StreamEvent,
    ToolCall,
)
from .react import ReActAgent
from .reflexion import Critique, ReflexionAgent
from .supervisor import SubAgent, SupervisorAgent
from .types import AgentResult, Step, Tool

__all__ = [
    "AgentResult",
    "AgentRequest",
    "BudgetDecision",
    "BudgetExceeded",
    "BudgetLimits",
    "AnthropicProvider",
    "Critique",
    "ContextAssembly",
    "ContextFragment",
    "ContextProvider",
    "DurableRunError",
    "EFFORT_LEVELS",
    "describe_effort",
    "effort_to_params",
    "normalize_effort",
    "FailoverProvider",
    "FakeProvider",
    "LLMProvider",
    "JournalEvent",
    "LLMResponse",
    "Message",
    "OllamaProvider",
    "OpenAICompatProvider",
    "OrchestrationPlan",
    "OrchestrationResult",
    "OrchestratorAgent",
    "OrchestratorSubAgent",
    "Plan",
    "PlanCache",
    "PlannerExecutorAgent",
    "ReActAgent",
    "RunBudget",
    "RunJournal",
    "ReflexionAgent",
    "repository_fingerprint",
    "Step",
    "StreamEvent",
    "SubAgent",
    "Subtask",
    "SubtaskResult",
    "SupervisorAgent",
    "Tool",
    "ToolCall",
    "assemble_context",
]
