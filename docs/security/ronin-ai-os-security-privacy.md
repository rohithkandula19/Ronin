# Ronin AI OS — security & privacy model

## Preserved guarantees (unchanged by this foundation)

- **Destructive safety floor.** `run_code_agent` wraps any supplied approval
  callback so `.ronin/settings.json` deny-rules and `is_floored_tool_call`
  destructive commands are enforced in-process. A web/HTTP `gate_cb` cannot
  waive them; `console=None` without a gate is default-deny. A fail-closed
  drift guard raises if a mutating tool reaches the toolbelt ungated.
- **Trust gates.** Repo-committed hooks/plugins/`mcp.json` never auto-execute
  (content-hash trust store). All MCP tools are `sensitive=True`; server
  `readOnlyHint` is deliberately ignored.
- **Fail-closed sandboxes.** A requested-but-unavailable backend refuses rather
  than running on the host.

## New controls in this foundation

### Authentication (API v1)
- Passwords hashed with PBKDF2-HMAC-SHA256 (200k iterations, per-user salt);
  **plaintext is never stored** (test-enforced).
- Session tokens are random; only their SHA-256 hash is persisted; logout
  revokes. Login returns an identical error for unknown-email and wrong-password
  (no account enumeration; test-enforced).

### Authorization
- Least privilege: users only see their own workspaces, world entries, memory,
  and audit events. Cross-owner memory access returns 404 (test-enforced).

### Data boundaries (Vault)
- Industry memory is isolated to its world — healthcare memory cannot surface
  in coding (test-enforced at the store and through the HTTP API).
- Organization memory never surfaces in personal workspaces.
- Nothing is training-eligible by default; eligibility needs an explicit
  consent reference; credential/minor/health data can never be eligible.
- Cross-world transfer requires a preview plus explicit confirmation.
- Every recall is written to a queryable usage audit ("why was this used?").

### Training-data governance (Forge)
- Per-item provenance (source, license, consent, redaction state, sensitivity).
- Redaction returns findings and never a "safe" verdict — a "clean" scan means
  no pattern matched, **not** that no PII exists; human review is required
  before an item is eligible.
- Health/minor/credential sensitivity and proprietary licenses exclude items.

### Audit
- Append-only `aios_audit_events`; register/login/logout, workspace create,
  world enter, memory add/delete all recorded.

## Threat areas and current status

| Area | Status | Note |
|---|---|---|
| Auth / session mgmt | IMPLEMENTED | pbkdf2 + hashed tokens + revoke |
| Cross-workspace / cross-owner access | IMPLEMENTED | least-privilege, tested |
| Cross-industry memory leakage | IMPLEMENTED | Vault isolation, tested |
| Training-data leakage | IMPLEMENTED | consent gate + sensitivity exclusions |
| Destructive floor bypass via web | PRESERVED | in-process wrap, cannot be waived |
| Prompt / indirect prompt injection | PARTIAL | `packages/hardening` scanners exist; per-world injection suites are next |
| Markdown/XSS on model output rendering | NOT_IMPLEMENTED | web render sanitization is a next-stage task |
| CSRF / CORS / rate limiting (v1) | PARTIAL | token auth (no cookies) sidesteps CSRF; rate-limit abstraction is next-stage |
| Dependency vulnerabilities | ONGOING | Dependabot active on the repo |
| Secrets at rest (legacy) | KNOWN ISSUE | `config.py` ships a dev FERNET_KEY default — flagged, must be set in any real deployment |

## Privacy

Every model request should be attributable to: provider, data included, whether
data left the device, tools used, memory used, and the industry policy applied.
The Vault's recall audit and the model/adapter registries supply the memory,
model, and policy legs today; the full per-request egress report is a
next-stage addition wired at the Core call site.
