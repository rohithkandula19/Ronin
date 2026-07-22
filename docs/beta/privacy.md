# Ronin AI OS — privacy & data handling (beta)

**Status: operational description of implemented behavior + DRAFT policy.**
The legally-binding privacy policy is `legal-terms-DRAFT.md`
(DRAFT_REQUIRES_LEGAL_REVIEW). This document describes what the system
actually does, as enforced by tested code.

## Principles (enforced in code)

1. **Consent-gated training.** No conversation, uploaded file, private-repo
   content, or healthcare data is ever used for training without explicit,
   recorded, revocable consent. `ronin_vault` marks memory training-eligible
   only when consent is `owner_self` (or an equivalently explicit grant); the
   Forge pipeline (`training/`) refuses ineligible items. Revocation drops
   eligibility.
2. **Isolation by default.** Memory, storage, and RBAC are org-scoped.
   Cross-org and cross-industry reads are denied fail-closed; there are
   explicit tests for cross-owner and cross-org denial in `ronin_vault`,
   `ronin_storage`, and `ronin_identity`.
3. **Secrets and sensitive data are never logged.** `ronin_observability`
   runs a redaction filter on every log record and its fields; secret/token/
   credential-shaped values and sensitive-keyed fields (password, secret,
   token, key, authorization, PHI markers) are replaced with `***` before a
   record is emitted. `ronin_support` notifications redact bodies before
   recording/sending. Document processing (`ronin_storage.docproc`) runs a
   redaction pass so downstream never sees raw secret/PII-shaped content.
4. **Healthcare is not diagnostic.** Healthcare workflows do not diagnose or
   prescribe; PHI is never logged unredacted and is never training-eligible
   without explicit consent.
5. **Data minimization for credentials.** No plaintext credentials are stored.
   `ronin_identity` stores only salted hashes for API keys / verifiers and
   exposes verify(); raw values are never persisted.

## Data categories

| Category | Stored where | Retention | Training-eligible? |
|---|---|---|---|
| Account (email, org membership) | identity store | account lifetime | no |
| Memory / notes | vault (org-scoped) | per retention policy | only with explicit consent |
| Uploaded documents | storage (org-namespaced, quota'd) | per retention policy | only with explicit consent |
| Usage metering | billing (append-only) | billing period + audit window | no |
| Audit log | api/audit | audit window | no |
| Logs / metrics | observability (redacted) | short operational window | no |

## Subject rights (design)

Export and deletion are per-org and rely on the isolation invariant above.
Deletion of a source drops its derived citations without leaving dangling
references (`ronin_research` behavior). Full DSAR tooling for a public launch
is NOT_IMPLEMENTED and is a stage-2+ item.

## What is NOT claimed

- No third-party data processors are engaged (no deployment, no live email/SMS,
  no payment processor). Those integrations are BLOCKED_CREDENTIALS.
- Cross-border transfer, DPA, and retention specifics require legal review
  (see `legal-terms-DRAFT.md`).
