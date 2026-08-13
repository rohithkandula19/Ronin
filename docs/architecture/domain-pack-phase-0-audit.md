# Phase 0: Domain Pack Contract and Domain Audit

**Status:** proposal for review. No runtime contract is implemented by this
document.

## Scope and method

This audit covers every production Python module beneath the three requested
package roots, including package export modules and provider submodules:

1. `packages/agent-patterns/src/ronin_agent_patterns` (21 modules)
2. `packages/hardening/src/ronin_hardening` (8 modules)
3. `packages/memory/src/ronin_memory` (4 modules)

It also inspected the required product and architecture surfaces: `README.md`,
`docs/agent_control_plane.md`, `docs/ORCHESTRATOR.md`,
`docs/FAITHFULNESS.md`, and the `/os/code`, `/os/research`,
`/os/healthcare`, and `/os/education` web worlds.

### Classification terms

- **Domain-agnostic:** its public contract can serve a domain without encoding
  software-repository semantics.
- **Coding-specific:** its normal public behavior assumes source code,
  repositories, files, patches, or software-engineering evidence.
- **Unclear:** reusable mechanics coexist with a mandatory or default
  dependency that is coding-specific, provider-specific, or insufficiently
  scoped to classify safely.

## 1. Module classification

| Module | Classification | Evidence and consequence |
| --- | --- | --- |
| `agent-patterns/__init__.py` | Domain-agnostic | Re-exports generic agent, provider, context, budget, and tool contracts. Its module prose is coding-oriented but exports do not impose a coding domain. |
| `agent-patterns/base.py` | Domain-agnostic | Tool argument adaptation and response parsing have no repository or code requirement. |
| `agent-patterns/contracts.py` | Domain-agnostic | `AgentRequest`, attributed `ContextFragment`, and bounded context assembly are generic. It has trust labels, but no pack identity or evidence policy. |
| `agent-patterns/durable.py` | Domain-agnostic | SQLite journal, checkpoint, and budget dimensions are reusable execution primitives. |
| `agent-patterns/effort.py` | Domain-agnostic | Normalizes provider reasoning effort, independent of task domain. |
| `agent-patterns/orchestrator.py` | Domain-agnostic | Planner, dependency waves, role prompts, tool subsets, and provider routing are injected. The library does not require engineering roles. |
| `agent-patterns/plan_cache.py` | Coding-specific | Cache identity is a Git/repository fingerprint and filesystem walk rooted in a project. A research corpus or clinical workspace cannot supply its own freshness identity. |
| `agent-patterns/planner_executor.py` | Unclear | The planner/executor loop is generic, but its only persistence integration is the repository-bound `PlanCache`. It needs an injected task-state/freshness strategy before it can claim domain neutrality. |
| `agent-patterns/providers/__init__.py` | Domain-agnostic | Pure provider exports. |
| `agent-patterns/providers/anthropic_provider.py` | Domain-agnostic | Provider wire translation and native token counting are task-neutral. |
| `agent-patterns/providers/base.py` | Domain-agnostic | Neutral messages, tool calls, responses, streaming, and a documented request-counting fallback. |
| `agent-patterns/providers/failover.py` | Domain-agnostic | Provider selection and fallback have no domain assumptions. |
| `agent-patterns/providers/fake.py` | Domain-agnostic | Test provider only. |
| `agent-patterns/providers/openai_compat.py` | Domain-agnostic | OpenAI-compatible wire translation and token-counting fallback are task-neutral. |
| `agent-patterns/providers/repetition.py` | Domain-agnostic | Detects repeated generated text, regardless of subject. |
| `agent-patterns/providers/text_tools.py` | Domain-agnostic | Conservatively recovers structured calls emitted as text and only accepts registered names. It has no coding-tool vocabulary. |
| `agent-patterns/react.py` | Domain-agnostic | The loop accepts caller-provided system prompt, tools, hooks, context providers, and budgets. It needs a pack boundary above it to prevent callers from bypassing policy. |
| `agent-patterns/reflexion.py` | Domain-agnostic | Generic act, critique, retry mechanics. Acceptance criteria are supplied by the critic prompt. |
| `agent-patterns/supervisor.py` | Domain-agnostic | Delegation is based on injected sub-agent role, prompt, provider, and tools. |
| `agent-patterns/token_counting.py` | Domain-agnostic | Counting helpers are provider/request mechanics. The byte-based fallback is explicitly labeled as an estimate in its source and architecture document. |
| `agent-patterns/types.py` | Domain-agnostic | Generic `Tool`, trace, and result shapes. `sensitive: bool` is too coarse to express a domain risk policy, but does not make the type coding-specific. |
| `hardening/__init__.py` | Domain-agnostic | Re-exports independent safety primitives. |
| `hardening/faithfulness.py` | Coding-specific | It extracts code symbols, file paths, function calls, and defaults evidence tools to repository/file actions. It cannot prove a research citation, source version, or medical-document claim. |
| `hardening/guardrails.py` | Domain-agnostic | Generic allowlist and human approval queue. It is intentionally simple and lacks durable/domain-aware authorization context. |
| `hardening/injection.py` | Domain-agnostic | Prompt-injection and output-leak scanning are content-domain neutral. Regex and optional classifier scores are detection signals, not proofs. |
| `hardening/secret_scanner.py` | Domain-agnostic | Credential leak detection can protect any domain, although its built-in patterns target common developer-service credentials. |
| `hardening/token_budget.py` | Domain-agnostic | Token/cost accounting is task-neutral. Its costs come from an explicit table and it fails closed when a cost cap has unknown pricing. |
| `hardening/tracing.py` | Domain-agnostic | Structured trace/audit helpers do not require code artifacts. |
| `hardening/validation.py` | Unclear | Schema validation is generic, but `OutputValidator.call()` constructs an Anthropic client directly. The validation contract can serve every domain; its mandatory execution path is not provider-agnostic. |
| `memory/__init__.py` | Domain-agnostic | Re-exports memory primitives, without adding domain behavior. |
| `memory/long_term.py` | Domain-agnostic | Namespaced persistent records and pluggable retrieval are generic. The default Jaccard ranker is a documented lexical heuristic for development/test, not semantic retrieval or a relevance guarantee. |
| `memory/preferences.py` | Unclear | The key-value store is generic, but extraction is hard-wired to Anthropic and has no domain consent, sensitivity, provenance, or retention contract. |
| `memory/short_term.py` | Unclear | Rolling conversational memory is generally useful, but it sends compaction to Anthropic and uses an undocumented-in-user-facing-docs `len(text) // 4` trigger. That is an estimate, not a provider token count or hard context guarantee. |

### Audit summary

Of the 33 modules in scope, 26 are domain-agnostic, 3 are coding-specific,
and 4 are unclear. This is a classification of module contracts, not a claim
that a complete second-domain runtime can be assembled today. The missing
boundary is the integration layer that applies domain policy to the otherwise
generic kernel.

## Existing pack foundation

Ronin already has a closely related, real foundation: the Industry Pack SDK
(`packages/industry-sdk`) and `industry-packs/{coding,education,healthcare}`.
`PackManifest` currently defines metadata, supported roles/locales, allowed
model capabilities/tools, blocked capabilities, required policy files, and
required evaluation suites. `PackRegistry.activate()` validates those static
references and refuses unhealthy packs.

This is not yet an execution contract. A manifest does not:

- construct the `Tool` objects used by `ReActAgent`;
- convert a tool call into a risk-classified action and approval decision;
- scope the actual system prompt or context providers;
- require, attach, or verify evidence citations on an agent result;
- bind memory to pack, workspace, user, consent, retention, and sensitivity;
- make the CLI agent or the API's coding runtime consume the selected pack;
- block an agent run when its pack's required eval evidence is stale or absent.

The domain-pack proposal below evolves that existing manifest instead of
creating a parallel registry. `industry-packs` remains the on-disk directory
and `PackManifest` remains the activation-facing metadata layer.

## 2. Proposed domain-pack contract: `domain-pack/v1`

### Design rules

1. A pack is selected before any provider request or tool construction.
2. A pack supplies capability declarations and policy data; it never supplies
   unrestricted arbitrary Python from its manifest.
3. Every executable tool is registered by the host under a stable tool id.
   The pack references those ids and adds policy metadata. An unknown reference
   is a load failure, never an omitted restriction.
4. The runtime emits evidence records, approvals, and audit events as typed
   data. Text in an agent answer cannot count as evidence by itself.
5. Estimates are explicit. A pack may use an estimated token count only when
   the selected provider lacks a native counter; the result must carry
   `kind: estimated` and its method. An estimate is never treated as a hard
   provider limit.

### On-disk shape

```text
industry-packs/<id>/
  manifest.yaml                 # existing activation metadata, schema v2
  prompts/
    system.md                   # required base behavioral scope
    <role>.md                   # optional role overlays
  policies/
    <policy>.yaml               # required policy documents
  evidence.yaml                 # required source/evidence behavior
  tools.yaml                    # references host registered tools and risk rules
  memory.yaml                   # isolation, consent, retention, sensitivity
  evals/<suite>.jsonl           # existing deterministic gates
```

### Manifest extension schema

The following is the proposed concrete YAML shape. Fields marked `required`
must be present for an enabled pack; `high` risk packs must also declare at
least one deny rule and at least one approval rule.

```yaml
schema_version: domain-pack/v1                 # required
id: research                                   # existing required fields remain
name: Research
version: 1.1.0
status: beta
risk_level: medium
supported_roles: [researcher, analyst]
supported_countries: [US]
supported_languages: [en]
allowed_model_capabilities: [text, retrieval, structured_output]
blocked_capabilities: [unsourced_claim]
required_policies: [research-integrity]
required_eval_suites: [research-grounding]

prompt:                                        # required
  base: prompts/system.md
  role_overlays:
    researcher: prompts/researcher.md
  allowed_context_kinds: [project_memory, retrieved_source, user_document]
  untrusted_context_kinds: [retrieved_source, user_document]
  forbidden_instruction_origins: [retrieved_source, user_document]

tools:                                         # required, can be an empty list
  - id: research_search
    operation: retrieve
    risk: read
    approval: never
    capabilities: [retrieval]
  - id: export_brief
    operation: publish_artifact
    risk: external
    approval: explicit
    capabilities: []
  - id: prescribe_treatment
    operation: clinical_action
    risk: prohibited
    approval: prohibited
    capabilities: []

policy:                                        # required
  default_action: deny
  approval_rules:
    - when: "risk in [write, external, irreversible, sensitive]"
      decision: require_human
  deny_rules:
    - when: "operation == clinical_action"
      decision: deny
      reason: "This information pack cannot take clinical action."

evidence:                                      # required
  mode: citations_required                     # none | provenance_required | citations_required
  minimum_sources: 2
  source_kinds: [web, uploaded_document, dataset]
  citation_format: source_id
  claims_requiring_evidence: [factual, quantitative, recommendation]
  on_insufficient_evidence: abstain
  verifier: source_id_and_span                 # exact verifier selected by host registry

memory:                                        # required
  namespace_template: "{workspace_id}:{pack_id}:{user_id}"
  allowed_sensitivity: [public, internal]
  retention_days: 90
  consent_required_for_write: true
  cross_pack_recall: deny

output:                                        # required
  artifact_types: [research_brief]
  schema: schemas/research_brief.json
  require_policy_disclosure: false
```

The values in `when` are a deliberately limited expression vocabulary, parsed
by the host. They are not Python expressions. The host owns the vocabulary and
must reject unknown predicates, operations, risks, capabilities, evidence
verifiers, and artifact schemas at pack activation.

### Runtime interface

The manifest describes intent. A host registry supplies implementations through
the following proposed interface. This is a specification, not source code to
add in Phase 0.

```python
class DomainPack(Protocol):
    manifest: DomainPackManifest

    def resolve_prompt(self, session: DomainSession,
                       context: ContextAssembly) -> ScopedPrompt:
        """Return the exact base/role prompt plus allowed attributed context."""

    def resolve_tools(self, session: DomainSession) -> list[Tool]:
        """Resolve only host-registered tools declared in manifest.tools."""

    def classify_action(self, tool_name: str, arguments: dict[str, Any]) -> DomainAction:
        """Map each call to operation, targets, reversibility, externality, and risk."""

    def authorize(self, action: DomainAction, session: DomainSession) -> Authorization:
        """Return allow, require_human, or deny before a handler can run."""

    def evidence_requirements(self, task: DomainTask) -> EvidenceRequirements:
        """Select claim classes, source types, minimums, and abstention behavior."""

    def verify_result(self, result: AgentResult,
                      evidence: list[EvidenceRecord]) -> VerificationReport:
        """Verify required citations/provenance and output schema before release."""

    def memory_scope(self, session: DomainSession) -> MemoryScope:
        """Return pack/workspace/user scope, consent, retention, and sensitivity limits."""
```

The kernel adapter contract is equally explicit:

```python
def run_domain_agent(
    *,
    pack: DomainPack,
    session: DomainSession,
    task: DomainTask,
    provider: LLMProvider,
    journal: RunJournal | None,
    budget: RunBudget | None,
) -> VerifiedDomainResult:
    """Only supported entry point for a pack-scoped agent run.

    Required sequence:
    activate pack -> resolve bounded context -> resolve scoped prompt/tools ->
    pre-tool authorization -> ReAct execution -> evidence verification ->
    policy post-check -> durable audit result.
    """
```

`DomainSession` must contain `pack_id`, `workspace_id`, `user_id`, role, locale,
consent references, and an immutable policy/pack version digest. `DomainAction`
must carry `tool_id`, operation, normalized target(s), risk, reversibility,
externality, and cost known/unknown state. `EvidenceRecord` must carry stable
source id, source kind, locator/span when available, retrieval time, trust,
and a content digest. `VerifiedDomainResult` must include the verification
report, citations/provenance, approval decisions, and an audit correlation id.

### Contract mapping to the requested concerns

| Concern | Contract mechanism | Fail-closed behavior |
| --- | --- | --- |
| Tool set | `tools.yaml` references host-registered ids only; `resolve_tools()` returns the resulting `Tool` list. | Unknown tool or capability reference prevents activation. |
| Approval and risk | Each tool declares operation/risk/approval; `classify_action()` and `authorize()` run before every call. | Unknown action classification or policy rule is denied. `prohibited` cannot be approved. |
| Evidence and citations | `evidence.yaml`, `EvidenceRecord`, and `verify_result()` specify source types, citation format, verifier, and abstention. | Required evidence missing, malformed, unverifiable, or below minimum produces an abstention/held result, never a verified claim. |
| System-prompt scoping | Prompt files plus allowed/untrusted context kinds are resolved before the first provider call. | Context from a forbidden origin is omitted; untrusted material is delimited and cannot alter policies/tools. |
| Memory | `memory.yaml` and `memory_scope()` bind every read/write to pack/workspace/user/consent/sensitivity. | Missing consent, invalid scope, or cross-pack recall is denied. |
| Evaluations | Existing required pack suites remain activation/promotion evidence; runtime records the evaluated pack version. | Missing required suite or stale/failed promotion evidence blocks the configured release state. |

## 3. Concrete current-code breakage list

This list describes what would break, change behavior, or be bypassed if a
second domain pack were added today without the proposed runtime contract. It
uses current repository code only.

1. **The terminal execution entry point always becomes a coding agent.**
   `run_code_agent()` in `packages/cli/src/ronin_cli/code_mode.py` builds the
   coding toolbelt, coding prompt, repository context, file/path protection,
   patch verification, and coding-specific approval UI. A research or biology
   manifest cannot replace those inputs, so a second pack cannot run through
   the main terminal loop without inheriting file editing and code instructions.

2. **Pack activation is disconnected from agent execution.** The API can call
   `PackRegistry.activate()` and expose the manifest through `/api/v1/worlds`,
   but neither `ReActAgent` nor `run_code_agent()` accepts a pack/session.
   Selecting `education` or `healthcare` therefore does not scope an agent's
   system prompt, tools, provider call, journal, or memory.

3. **Current tool approval is named after coding tools, not domain actions.**
   `SENSITIVE_TOOLS`, `is_floored_tool_call`, and the web `coding_runtime.py`
   classify `write_file`, `edit_file`, and `run_command`. A pack tool such as
   `send_for_review`, `export_report`, or `place_order` has no pack-declared
   risk-to-approval mapping. `Tool.sensitive` offers only a boolean and cannot
   express prohibited clinical actions, data sensitivity, reversibility, or
   required approver roles.

4. **Coding safety behavior would either be duplicated or accidentally reused.**
   The web coding world imports CLI-only `code_tools`, `approvals`, and roles.
   A second web world must either copy this coding seam, leave itself with only
   a static manifest, or incorrectly apply file/shell rules to non-file actions.

5. **Context sources are repository-shaped.** `ronin code` injects repository
   instructions (`RONIN.md`, `CLAUDE.md`, `AGENTS.md`), project memory, and
   repository retrieval. The generic `ContextProvider` contract does not carry
   pack id, user/workspace scope, source type, retention, citation locator, or
   a pack rule for whether a source is trusted.

6. **The current faithfulness gate cannot validate non-code evidence.**
   `ronin_hardening.faithfulness` rewards lexical overlap and code symbol/file
   presence from tools such as `read_file` and `search_files`. It does not
   require citations, distinguish source quality, preserve source spans, or
   enforce healthcare/education/research abstention rules. Reusing it as a
   domain proof would overstate what it verifies.

7. **Memory isolation is incomplete at the agent-kernel layer.**
   `ronin_memory.LongTermMemory` accepts arbitrary string namespaces, and
   preference/short-term memory have no pack/workspace/user/consent/retention
   requirements. The separate API vault does isolate `world` data, but that
   scope is not propagated into `ronin_memory` or `ReActAgent` runs.

8. **Existing legacy memory compaction has an undocumented estimate.**
   `ShortTermMemory._approx_tokens()` uses `len(text) // 4` to decide when to
   summarize. This is a heuristic estimate, not an API token count or a safe
   hard limit. It must be replaced or clearly labeled and routed through the
   provider-aware token-counting contract before a domain pack relies on it.

9. **Two memory modules and `OutputValidator` hard-code Anthropic calls.**
   `ShortTermMemory`, `UserPreferenceMemory`, and
   `ronin_hardening.validation.OutputValidator.call()` bypass the provider
   abstraction. A pack selected for local/offline, OpenAI-compatible, or other
   providers would silently lose the project’s provider-agnostic promise on
   those paths.

10. **Planner persistence assumes a Git repository.** `PlanCache` fingerprints
    Git state or a project filesystem. A research collection, health record
    workspace, or education course needs a declared corpus/artifact freshness
    digest. `PlannerExecutorAgent` currently exposes only the repository cache.

11. **The write workflow is a software-engineering workflow.** Control-plane
    and CLI orchestration use researcher/implementer/reviewer/tester roles,
    isolated Git worktrees, diffs, commands, and test evidence. The core
    orchestrator can accept different role prompts, but no pack can currently
    declare role lifecycle, artifact handoff schema, or domain acceptance
    evidence.

12. **Pack evaluations do not gate an actual agent turn.** The Industry SDK
    discovers policy/eval files and can evaluate a responder, but the normal
    agent entry points do not record which pack version and eval evidence
    authorized an execution or release. The required suites are a registry
    health check, not a runtime postcondition.

13. **The non-coding worlds are not interactive domain-agent clients yet.**
    `/os/research`, `/os/healthcare`, and `/os/education` are expressly
    labeled sample/offline interfaces. Their API workflows are deterministic
    artifacts for limited paths, not a common pack-scoped `ReActAgent` runtime.
    Their client-side term checks are presentation behavior and cannot be the
    enforcement boundary for future tools.

14. **Audit records do not have a common domain evidence envelope.** Durable
    agent journals track safe lifecycle metadata and the API records world
    events, but neither requires a pack id/version digest, evidence records,
    citation verifier result, or policy decision for every finalized result.

## Checkpoint result

This checkpoint is complete when the proposed contract and breakage list have
been reviewed. No implementation, migration, or test was run because the user
requested a design-only stop after Step 3.

### Explicit deferrals

- No `DomainPack` Python types, YAML validation, adapters, tool registry, or
  CLI/API wiring were added. They depend on review of this contract.
- No existing Industry Pack manifest was migrated. Doing so before contract
  approval would create an unreviewed compatibility target.
- No test suite was run. This phase changes one Markdown design document only;
  the next implementation phase needs an agreed test plan and exact pass/fail
  reporting before it begins.
