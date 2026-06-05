# Integrations

ronin can use external tools three ways — and each is **one command** to set up.
Everything below gives the agent new tools it can call mid-conversation, gated by
your normal approvals.

| Route | For | Add it with |
|---|---|---|
| 🔌 **Local MCP** | servers you run locally (GitHub, Postgres, filesystem…) | `ronin mcp install <name>` |
| 🌐 **Remote MCP** | hosted servers (Linear, Atlassian, Cloudflare, GitHub-cloud…) | `ronin mcp add-remote <name> <url>` |
| 🧩 **Plugins** | a few lines of Python — anything with an API | `ronin plugin add <name>` / `ronin plugin new <name>` |

All plugin handlers are **crash-proofed** at load time: a flaky API or a bug
surfaces as a structured error the agent can recover from, never a crash.

---

## 🔌 MCP servers (Anthropic's tool protocol)

ronin speaks the same protocol Claude does, over **stdio** (local) or **HTTP/SSE**
(remote).

```bash
ronin mcp catalog                 # browse popular servers
ronin mcp install github          # resolves package + env for you
ronin mcp add NAME COMMAND [ARGS] # any local server
ronin mcp add-remote NAME URL     # any hosted server (-H 'Authorization' uses $MCP_TOKEN)
ronin mcp list                    # connect + show each server's tools
ronin mcp remove NAME
```

Secrets are passed via `--env KEY=VALUE` (a bare `KEY` inherits from your shell, so
the token never appears on the command line). Example — GitHub:

```bash
export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_…
ronin mcp install github
```

### Catalog (24 servers)

| install | what it adds | needs |
|---|---|---|
| `github` | Issues, PRs, repo & code search, file ops | GITHUB_PERSONAL_ACCESS_TOKEN |
| `gitlab` | GitLab issues, MRs, repo ops | GITLAB_PERSONAL_ACCESS_TOKEN |
| `gmail` | Read, search & send Gmail | — |
| `slack` | Read channels & post messages | SLACK_BOT_TOKEN, SLACK_TEAM_ID |
| `filesystem` | Read/write files under a directory | — |
| `postgres` | Query Postgres (append connection URL) | — |
| `sqlite` | Query a local SQLite database | — |
| `gdrive` | Search & read Google Drive files | — |
| `brave-search` | Web search via the Brave API | BRAVE_API_KEY |
| `fetch` | Fetch a URL → clean markdown | — |
| `puppeteer` | Headless-browser automation | — |
| `memory` | A persistent knowledge-graph memory | — |
| `notion` | Read & update Notion pages/databases | NOTION_API_KEY |
| `git` | Inspect/operate a local git repo | — |
| `time` | Current time & timezone conversion | — |
| `sequentialthinking` | A structured reasoning scratchpad | — |
| `everything` | Reference server (all MCP features) | — |
| `google-maps` | Geocoding, directions, places | GOOGLE_MAPS_API_KEY |
| `redis` | Read/write a Redis store | — |
| `aws-kb` | Query an AWS Bedrock knowledge base | AWS keys |
| `stripe` | Customers, payments, invoices, products | STRIPE_SECRET_KEY |
| `exa` | Neural web search | EXA_API_KEY |
| `obsidian` | Read & search an Obsidian vault | OBSIDIAN_API_KEY |
| `playwright` | Browser automation via Playwright | — |

Any other server works too — `ronin mcp add <name> <command> [args]`.

---

## 🧩 Plugins (your own Python tools)

```bash
ronin plugin library              # browse the 70 built-ins
ronin plugin search <keyword>     # find one (e.g. 'finance', 'word')
ronin plugin add <name>           # install a built-in
ronin plugin new <name>           # scaffold your own (ready to edit)
ronin plugin update [name]        # refresh installed library plugins (bug fixes)
ronin plugin remove <name>
ronin plugins                     # what's loaded (✓ in `library` marks installed)
```

### Write your own in ~10 lines

`.ronin/plugins/mytool.py`:

```python
from ronin_agent_patterns import Tool

def my_tool(query: str) -> dict:
    # call any API, run any logic
    return {"result": query.upper()}

def register_tools():
    return [Tool(
        name="my_tool",
        description="What it does (the agent reads this).",
        input_schema={"type": "object",
                      "properties": {"query": {"type": "string"}},
                      "required": ["query"]},
        handler=my_tool,
    )]
```

Drop it in and the agent picks it up next run. `ronin plugin new <name>` writes
exactly this shape for you, with a working example inside.

### Built-in library (70 plugins, no keys)

| plugin | what it does |
|---|---|
| `weather` | Current weather for any city (open-meteo) |
| `air_quality` | Air quality (AQI) for a city |
| `sun_times` | Sunrise/sunset for a city |
| `public_holidays` | Official holidays by country/year |
| `currency` | Convert between currencies (live rates) |
| `crypto_price` | Spot crypto price + 24h change |
| `stock_price` | Latest quote for a stock ticker |
| `github_repo` | Stars/forks/language for a repo |
| `github_user` | Public GitHub profile stats |
| `github_trending` | Recently-rising GitHub repos |
| `github_releases` | Latest release of a repo |
| `npm_package` | npm package info (version, license) |
| `pypi_package` | PyPI package info (version, summary) |
| `dns_lookup` | Resolve DNS records |
| `url_check` | HTTP status & timing of a URL |
| `rss_feed` | Latest items from an RSS/Atom feed |
| `text_stats` | Word/char counts + reading time (offline) |
| `slugify` | Text → URL slug (offline) |
| `case_convert` | snake/camel/kebab/pascal/title (offline) |
| `diff_text` | Unified diff of two texts (offline) |
| `uuid_gen` | Generate UUIDs (offline) |
| `password_gen` | Generate a strong password (offline) |
| `base64_tool` | Base64 encode/decode (offline) |
| `url_encode` | URL percent-encode/decode (offline) |
| `hash_text` | md5/sha256 a string (offline) |
| `jwt_decode` | Decode a JWT header/payload (offline) |
| `regex_test` | Test a regex against text (offline) |
| `roman_numeral` | Int ↔ Roman numeral (offline) |
| `json_format` | Validate + pretty-print JSON (offline) |
| `timestamp` | Unix ↔ ISO time (offline) |
| `lorem_ipsum` | Placeholder text (offline) |
| `unit_convert` | Convert length/mass/temperature (offline) |
| `geo_distance` | Distance between two coordinates (offline) |
| `loan_payment` | Loan/mortgage monthly payment (offline) |
| `tip_split` | Tip + split a bill (offline) |
| `bmi` | Body mass index (offline) |
| `dice` | Roll dice in NdM notation (offline) |
| `morse_code` | Encode/decode Morse code (offline) |
| `random_color` | Random hex colors (offline) |
| `geocode` | Place name → coordinates |
| `weather_forecast` | Multi-day forecast for a city |
| `random_fact` | A random interesting fact |
| `qr_code` | Make a QR-code image for any text |
| `shorten_url` | Shorten a link (is.gd) |
| `translate` | Translate text between languages |
| `color_info` | Name + RGB/HSL of a hex color |
| `ip_info` | Geolocate an IP address |
| `wikipedia` | A topic summary from Wikipedia |
| `define` | Dictionary definitions of a word |
| `synonyms` | Synonyms for a word |
| `rhymes` | Words that rhyme with a word |
| `country_info` | Capital, population, currencies of a country |
| `book_search` | Find a book (Open Library) |
| `trivia` | A random trivia question + answer |
| `recipe` | Find a recipe by dish name |
| `cocktail` | A cocktail recipe by name |
| `pokemon` | Stats for a Pokemon |
| `anime` | Look up an anime (MyAnimeList) |
| `iss_location` | Live position of the ISS |
| `spacex_latest` | Most recent SpaceX launch |
| `dad_joke` | A random dad joke |
| `programming_joke` | A programming joke |
| `chuck_norris` | A Chuck Norris joke |
| `advice` | A random piece of advice |
| `quote` | A random inspirational quote |
| `dog_image` | A random dog photo |
| `age_guess` | Predict age from a name |
| `random_user` | Generate a fake user profile |
| `hackernews` | Top Hacker News stories |
| `scratchpad` | A persistent project notes scratchpad |

Most have short aliases (`fx`→currency, `btc`→crypto, `wiki`→wikipedia,
`b64`→base64_tool, `qr`→qr_code, `repo`→github_repo, …) — `ronin plugin search`
finds them.

---

## Which route should I use?

- The tool already has an **MCP server** (GitHub, Slack, Postgres, Notion…) → MCP.
- It's a **hosted** MCP endpoint (Linear, Atlassian…) → `mcp add-remote`.
- It's just an **API or some logic** and you want it fast → a plugin
  (`ronin plugin new`).
- You want it **today, no setup** → `ronin plugin add` from the 70 built-ins.
