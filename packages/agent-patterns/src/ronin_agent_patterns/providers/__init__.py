"""LLM provider abstraction.

Pick your model:

- ``AnthropicProvider`` — Claude (default, recommended).
- ``OpenAICompatProvider`` — OpenAI, Ollama, Together, Groq, Fireworks, vLLM,
  llama.cpp server, LM Studio — anything exposing the OpenAI ``/chat/completions`` API.

For tests: ``FakeProvider`` returns canned ``LLMResponse``s in order, no network.
"""
from .anthropic_provider import AnthropicProvider
from .base import LLMProvider, LLMResponse, Message, StreamEvent, ToolCall
from .failover import FailoverProvider
from .fake import FakeProvider
from .openai_compat import OllamaProvider, OpenAICompatProvider

__all__ = [
    "AnthropicProvider",
    "FailoverProvider",
    "FakeProvider",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "OllamaProvider",
    "OpenAICompatProvider",
    "StreamEvent",
    "ToolCall",
]
