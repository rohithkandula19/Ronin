# ronin-dialect

The canonical `<tool_call>` wire format, in exactly one place. Corpus generation
renders through it, the embedded runtime parses through it, and the protocol eval
extracts through it — so the three can never drift apart again (the drift risk
that motivated this package is documented in
`training/reports/valid_tool_json_root_cause.md`).

Stdlib only, zero dependencies. Golden round-trip tests
(`render → parse → identical call`) run offline in CI.
