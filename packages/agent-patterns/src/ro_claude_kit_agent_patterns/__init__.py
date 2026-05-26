"""Production-grade agent patterns for Claude — and any OpenAI-compatible LLM.

Four patterns shipped:
- ``ReActAgent`` — Reason-Act-Observe loop with tool retry and iteration cap.
- ``PlannerExecutorAgent`` — Plan-then-execute with checkpoint/resume.
- ``SupervisorAgent`` — Orchestrator that delegates to specialist sub-agents.
- ``ReflexionAgent`` — Act → critique → retry-with-feedback.

Plus shared types (``Tool``, ``AgentResult``, ``Step``) and providers
(``AnthropicProvider``, ``OpenAICompatProvider``, ``OllamaProvider``, ``FakeProvider``).
"""
from .planner_executor import Plan, PlannerExecutorAgent
from .providers import (
    AnthropicProvider,
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
    "AnthropicProvider",
    "Critique",
    "FakeProvider",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "OllamaProvider",
    "OpenAICompatProvider",
    "Plan",
    "PlannerExecutorAgent",
    "ReActAgent",
    "ReflexionAgent",
    "Step",
    "StreamEvent",
    "SubAgent",
    "SupervisorAgent",
    "Tool",
    "ToolCall",
]
