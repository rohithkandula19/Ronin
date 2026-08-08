"""Local MLX adapter: in-process generation on Apple silicon, no server.

Unlike every other adapter this one has no HTTP at all — it drives ``mlx_lm``
directly, which is the whole reason it exists. Running through llama.cpp's server
would work, but it costs a subprocess, a port, and a second copy of the weights in
RAM on a machine where RAM is the binding constraint.

Two consequences shape the design:

* **``mlx_lm`` is a heavy optional import.** It is imported inside
  :meth:`MLXClient.stream`, not at module scope, so ``import ronin.providers``
  stays free on Linux and in CI.
* **Generation is synchronous and blocking.** A tight ``for`` loop over a
  generator would starve the event loop and make interrupt handling a lie, so
  tokens cross into async through :func:`asyncio.to_thread` and a queue.

Capabilities default to ``native_tools=False``: base Qwen, Hermes and most local
GGUF/MLX builds have no tool-calling head, so the router puts them behind the
format shim. A build that *does* have one can say so in config.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from ..core.types import Message, Role, Text
from .base import ModelClient
from .types import (
    Capabilities,
    Completed,
    FinishReason,
    ModelDelta,
    ModelRequest,
    ProviderError,
    TextDelta,
    Usage,
)

#: What a local model can do unless config says otherwise. Narrow on purpose:
#: promising native tools we do not have produces a turn where the model chats
#: about calling a tool and nothing happens.
LOCAL_DEFAULTS = Capabilities(
    native_tools=False,
    parallel_tools=False,
    prompt_cache=False,
    thinking=False,
    max_context=32_768,
    vision=False,
)

#: A generation backend: given a prompt, yield token strings. Injected so the
#: adapter is testable without Apple silicon or a multi-gigabyte download.
Generator = Callable[[str, int], Iterator[str]]


class MLXClient:
    """Generates locally through ``mlx_lm``, or through an injected generator.

    ``generate`` exists for the tests and for anyone who wants to point this at a
    different local runtime; when it is ``None`` the real ``mlx_lm`` path is used.
    """

    provider_name = "mlx"

    def __init__(
        self,
        *,
        model: str,
        adapter_path: str | None = None,
        capabilities: Capabilities | None = None,
        generate: Generator | None = None,
        chat_template: Callable[[ModelRequest], str] | None = None,
    ) -> None:
        self._model = model
        self._adapter_path = adapter_path
        self._capabilities = capabilities or LOCAL_DEFAULTS
        self._generate = generate
        self._chat_template = chat_template or render_prompt
        self._loaded: tuple[Any, Any] | None = None

    def capabilities(self) -> Capabilities:
        return self._capabilities

    def _resolve_generator(self) -> Generator:
        if self._generate is not None:
            return self._generate
        return self._mlx_generator()

    def _mlx_generator(self) -> Generator:
        """Load ``mlx_lm`` and return a token generator over it."""
        try:
            from mlx_lm import load, stream_generate
        except ImportError as exc:
            raise ProviderError(
                "mlx-lm is not installed — `uv pip install mlx-lm` on Apple "
                "silicon, or point this role at an openai-compatible server instead",
                provider=self.provider_name,
                retryable=False,
            ) from exc

        if self._loaded is None:
            self._loaded = load(self._model, adapter_path=self._adapter_path)
        model, tokenizer = self._loaded

        def generate(prompt: str, max_tokens: int) -> Iterator[str]:
            for response in stream_generate(
                model, tokenizer, prompt=prompt, max_tokens=max_tokens
            ):
                yield str(response.text)

        return generate

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        """Generate locally, bridging the blocking token loop into async."""
        prompt = self._chat_template(req)
        generate = self._resolve_generator()
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=64)
        loop = asyncio.get_running_loop()
        failure: list[BaseException] = []

        def pump() -> None:
            """Runs on a worker thread; hands tokens back through the loop."""
            try:
                for token in generate(prompt, req.max_tokens):
                    asyncio.run_coroutine_threadsafe(queue.put(token), loop).result()
            except BaseException as exc:  # re-raised on the event loop below
                failure.append(exc)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        worker = asyncio.create_task(asyncio.to_thread(pump))
        parts: list[str] = []
        try:
            while True:
                token = await queue.get()
                if token is None:
                    break
                parts.append(token)
                yield TextDelta(token)
        finally:
            # A consumer that stops iterating (interrupt, budget) must not leave a
            # thread writing into a queue nobody reads.
            worker.cancel()

        if failure:
            raise ProviderError(
                f"local generation failed: {failure[0]}",
                provider=self.provider_name,
                retryable=False,
            ) from failure[0]

        text = "".join(parts)
        yield Completed(
            message=Message(
                role=Role.ASSISTANT,
                content_blocks=(Text(text),) if text else (),
            ),
            finish=FinishReason.STOP,
            # A local model costs no money and reports no usage. Estimating tokens
            # here would put a made-up number in the ledger; zero with a note is
            # honest, and `cost_usd` is genuinely zero.
            usage=Usage(),
            notes=("local model: token counts not reported by the runtime",),
        )


def render_prompt(req: ModelRequest) -> str:
    """A plain chat rendering for runtimes with no chat template applied.

    Deliberately boring and explicit. A model whose tokenizer *does* have a
    template should be given one via ``chat_template``; guessing a template is how
    a local model ends up quietly answering a differently-shaped prompt than it
    was trained on.
    """
    parts: list[str] = []
    head = "\n\n".join(part for part in (req.system, req.prefix) if part)
    if head:
        parts.append(f"System: {head}")
    for message in req.messages:
        label = {
            Role.USER: "User",
            Role.ASSISTANT: "Assistant",
            Role.SYSTEM: "System",
            Role.TOOL: "Tool",
        }[message.role]
        body = message.text
        for block in message.tool_results:
            body = f"{body}\n{block.content}" if body else block.content
        for use in message.tool_uses:
            body = f"{body}\n[called {use.name}]" if body else f"[called {use.name}]"
        if body:
            parts.append(f"{label}: {body}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


def _conforms(client: MLXClient) -> ModelClient:
    """Static proof that this adapter satisfies the protocol."""
    return client
