# ronin-mcp-servers

Reference MCP server templates. Read-only by default; write operations require explicit configuration. Auth via env vars.

## Status

| Server | Status |
|---|---|
| Postgres (read-only) | ✅ shipped |
| Stripe (read-only) | ✅ shipped |
| Linear (read-only) | ✅ shipped |
| Slack (read-only) | ✅ shipped |
| Notion (read-only) | ✅ shipped |
| Tavily web search (read-only) | ✅ shipped |

All six servers are read-only by design. Adding write paths should go through `ApprovalGate` from the `hardening` package.

## Tavily web search

```python
from ronin_mcp_servers import TavilyTools

tavily = TavilyTools(api_key="tvly-...")  # or set TAVILY_API_KEY
result = tavily.search("What is the ReAct pattern?", max_results=5, include_answer=True)
print(result["answer"])
for hit in result["results"]:
    print(f"  {hit['title']} — {hit['url']}")
```

Free tier at https://tavily.com gives 1000 searches/mo — perfect for a research agent.

## Stripe (read-only)

```python
from ronin_mcp_servers import StripeReadOnlyTools, stripe_tools

stripe = StripeReadOnlyTools(api_key="sk_live_...")  # or set STRIPE_API_KEY env var
customers = stripe.list_customers(limit=10, email="alice@example.com")
subs = stripe.list_subscriptions(customer_id=customers[0]["id"], status="active")

# Or get a name -> handler dict for direct registration:
handlers = stripe_tools(api_key="sk_live_...")
# handlers["stripe_list_customers"], etc.
```

Use a Stripe *Restricted Key* scoped to read-only resources. Never use a live secret key here.

## Linear (read-only)

```python
from ronin_mcp_servers import LinearReadOnlyTools, linear_tools

linear = LinearReadOnlyTools(api_key="lin_api_...")
issues = linear.list_issues(team_id="...", state="In Progress")
issue = linear.get_issue("ENG-123")
```

Auth uses Linear's personal API keys. Generate at https://linear.app/settings/api.

## Postgres MCP server

Read-only SQL with two layers of defense:
1. Safety check rejects non-SELECT statements, multi-statement queries, `SELECT ... INTO`, and any query containing destructive keywords.
2. Defense-in-depth: in production, also bind the server to a Postgres role with SELECT-only privileges.

### Direct use (as an in-process tool)

```python
from ronin_mcp_servers import PostgresQueryTool
import psycopg2

conn = psycopg2.connect(DATABASE_URL)
tool = PostgresQueryTool(connection=conn, max_rows=500)
rows = tool.query("SELECT id, email FROM users LIMIT 10")
```

Wire it into the agent-patterns toolkit:

```python
from ronin_agent_patterns import Tool

pg = Tool(
    name="postgres_query",
    description=tool.description,
    input_schema={
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
    },
    handler=tool.query,
)
```

### As an MCP server over stdio

```bash
pip install ronin-mcp-servers[mcp,postgres]
export DATABASE_URL=postgres://readonly_user:...@host:5432/db
python -m ronin_mcp_servers.postgres
```

Then point your MCP-aware client at the process.

### Safety check directly

If you want the safety check without the rest:

```python
from ronin_mcp_servers import is_readonly_sql

allowed, reason = is_readonly_sql(user_sql)
if not allowed:
    raise BadInput(reason)
```

## Tests

```bash
uv run --frozen pytest packages/mcp-servers -q
```

Tests run against in-memory sqlite — no Postgres or `psycopg2` install needed.
