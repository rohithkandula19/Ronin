# Security Policy

Thanks for helping keep **ronin** and its users safe.

## Supported Versions

Only the latest minor release line receives security updates.

| Version  | Supported |
| -------- | --------- |
| 0.58.x   | Yes       |
| < 0.58   | No        |

## Reporting a Vulnerability

**Please do not open public GitHub issues for security problems.**

Email a private report to: **rohithkandula937@gmail.com** with
`[SECURITY]` in the subject. Include, where possible:

- A description of the issue and its impact
- Steps to reproduce (PoC, sample input, command line, environment)
- The ronin version (`ronin version`) and Python version
- Any suggested fix or mitigation

You'll get an acknowledgement within **3 business days** and a more
detailed response within **7 business days**.

## Disclosure Timeline

We follow a **90-day coordinated disclosure** policy:

1. **Day 0** — report received, triage begins
2. **Day 0–7** — confirmation and severity assessment
3. **Day 7–80** — fix developed, tested, and released
4. **Day 90** — public disclosure, unless an extension is mutually agreed

Reporters are credited in release notes (let us know your preferred
name/handle, or if you'd rather stay anonymous).

## Scope

### In scope

- **Agent execution** — sandbox escapes, command injection, or unintended
  code-execution paths in the core agent loop (`ronin code`, `ronin chat`)
- **Tool guardrails** — bypasses of the tool allowlist, approval gates, or
  the filesystem sandbox
- **Secret handling** — leakage of API keys, tokens, or environment
  variables via logs, sessions, memory files, or error output
- **The secret scanner / write guard** — inputs that should be blocked
  but aren't

### Out of scope

- Third-party MCP servers and the services they connect to
- User-installed plugins and unofficial forks
- Issues requiring a pre-compromised host or attacker-controlled local config
- Issues only reproducible on unsupported versions
- Social engineering and DoS against infrastructure not operated by this project

## Safe Harbor

We will not pursue legal action against researchers who make a good-faith
effort to follow this policy, avoid privacy violations and service
disruption, and give us a reasonable window to remediate before disclosing.
