# Context Token Counting

Ronin compacts agent history before a provider request rather than waiting for a
context-window rejection. Counts apply to the rendered request: system prompt,
conversation, tool-call history, and tool schemas.

## Count authority

| Provider path | Count method | Label |
| --- | --- | --- |
| Anthropic | Messages `count_tokens` endpoint | `native` |
| OpenAI first-party | Responses `POST /responses/input_tokens` | `native` |
| Local model with a supplied tokenizer | Local tokenizer | `tokenizer` |
| Ollama, compatible, or custom endpoint without a tokenizer | UTF-8 fallback | `estimated` |

The fallback is `ceil(UTF-8 bytes / 3) + 4`, plus message and tool-envelope
overhead for a full request. It is intentionally conservative, but it is an
estimate, not a provider guarantee. Ronin reserves output capacity and treats a
provider context-length error as authoritative.

No remote token-count request is made in offline mode. Native counter failures
fall back locally and never prevent a completion request.

## Durable evidence

When a `RunJournal` is active, Ronin records `context_counted` events containing
only the token total, count kind, count method, and iteration. It never records
the prompt payload in this event. The latest count is also surfaced as
`latest_context_tokens` in run usage; it is observability metadata, not billable
provider usage.
