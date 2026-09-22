# Provider smoke-test checklist

Use this before shipping a provider change in Ronin.

1. Auth path succeeds with a dry-run request.
2. Streaming and non-streaming both return a usable completion.
3. Tool-call payloads parse without dropping arguments.
4. Rate-limit / timeout errors surface as typed failures, not raw traces.
5. Model alias resolution still maps to the intended backend.
