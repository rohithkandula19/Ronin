# apps/docs

Mintlify-powered documentation site for Ronin.

## Run locally

```bash
npm i -g mintlify     # one-time
cd apps/docs
mintlify dev          # http://localhost:3000
```

## Deploy

Connect this repo's `apps/docs` directory to Mintlify (free for OSS) at https://dashboard.mintlify.com.

## Content layout

```
apps/docs/
├── mint.json                       # site config + navigation
├── introduction.mdx
├── quickstart.mdx
├── concepts/
│   ├── agent-patterns.mdx          # patterns + decision tree (mermaid)
│   ├── evals.mdx                   # rubric + dataset + drift CI gate
│   ├── memory.mdx                  # three layers
│   ├── hardening.mdx               # injection / allowlist / approval / validation
│   └── mcp-servers.mdx             # all five servers
├── production-checklist.mdx
└── adrs.mdx                        # architecture decisions + when to question them
```

All content is plain MDX — easy to edit and easy to migrate to Nextra / Docusaurus / a custom Next.js site if Mintlify ever stops fitting.
