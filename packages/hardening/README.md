# ronin-hardening

Production hardening for Claude agents. The unglamorous stuff that decides whether your agent survives prod.

## Prompt-injection scanning

```python
from ronin_hardening import InjectionScanner

scanner = InjectionScanner()
result = scanner.scan(user_input)
if result.flagged:
    raise BadInput(f"injection detected: {result.hits}")
```

`InjectionScanner` ships a default pattern set covering instruction-override, persona-hijack, chat-template injection, and prompt-extraction attempts. Pass an `llm_classifier` callable for a second layer that catches novel attacks.

For high-risk inputs use the *dual-LLM* pattern: keep untrusted input out of the agent's planner LLM. Run it through a sandboxed reader LLM first.

`OutputLeakScanner` catches the other direction — system-prompt or secret leakage in agent output.

## Tool allowlist + approval gate

```python
from ronin_hardening import ToolAllowlist, ApprovalGate

allowlist = ToolAllowlist(allowed={"search", "fetch_doc"})
allowlist.assert_allowed(tool_name)  # raises PermissionError if not allowed

gate = ApprovalGate()
gate.register("delete_user", delete_user_handler)
pending = gate.request("delete_user", {"user_id": "alice"}, reason="GDPR cleanup")
# Surface pending.id to a human; on approval:
gate.execute(pending.id)
```

Allowlist blocks hallucinated or attacker-crafted tool names at dispatch time. Approval gates put a human in front of writes, money movement, and deletions.

## Output validation with retry

```python
from pydantic import BaseModel
from ronin_hardening import OutputValidator

class ExtractedPerson(BaseModel):
    name: str
    age: int

validator = OutputValidator(output_schema=ExtractedPerson, max_attempts=3)
person = validator.call(
    system="Extract a person from the text.",
    user_message="Alice is 30 and works at Acme.",
)
```

On validation failure the validator feeds the Pydantic error back to the model and retries up to `max_attempts`. Raises `ValidationFailure` if all attempts fail.

## Provider-agnostic tracing

```python
from ronin_hardening import TraceEmitter

emitter = TraceEmitter(sink=lambda event: langfuse.create_event(event.model_dump()))
trace_id = emitter.start_trace("research-agent", payload={"user": user_email})
emitter.emit(trace_id, "tool_call", "search", {"query": q})
emitter.end_trace(trace_id, "research-agent", {"output": result})
```

Events are PII-redacted by default (emails, SSNs, credit cards, API keys). Pass `extra_pii_patterns` for org-specific patterns. Set `redact=False` for trusted dev environments.

The event shape is provider-neutral — write an adapter for Langfuse, Helicone, OpenTelemetry, or your own logging stack.

## Tests

```bash
uv run --frozen pytest packages/hardening -q
```

No API key needed.
