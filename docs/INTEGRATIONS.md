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
ronin plugin library              # browse the 200 built-ins
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

### Built-in library (200 plugins, no keys)

| plugin | what it does |
|---|---|
| `weather` | Current weather for any city (open-meteo) |
| `github_trending` | Recently-rising GitHub repos |
| `scratchpad` | A persistent project notes scratchpad |
| `hackernews` | Top Hacker News stories |
| `currency` | Convert between currencies (live rates) |
| `crypto_price` | Spot crypto price + 24h change |
| `define` | Dictionary definitions of a word |
| `wikipedia` | A topic summary from Wikipedia |
| `ip_info` | Geolocate an IP address |
| `public_holidays` | Official holidays by country/year |
| `shorten_url` | Shorten a link (is.gd) |
| `qr_code` | Make a QR-code image for any text |
| `translate` | Translate text between languages |
| `npm_package` | npm package info (version, license) |
| `pypi_package` | PyPI package info (version, summary) |
| `stock_price` | Latest quote for a stock ticker |
| `country_info` | Capital, population, currencies of a country |
| `dad_joke` | A random dad joke |
| `sun_times` | Sunrise/sunset for a city |
| `color_info` | Name + RGB/HSL of a hex color |
| `unit_convert` | Convert length/mass/temperature (offline) |
| `random_user` | Generate a fake user profile |
| `iss_location` | Live position of the ISS |
| `air_quality` | Air quality (AQI) for a city |
| `github_user` | Public GitHub profile stats |
| `rhymes` | Words that rhyme with a word |
| `synonyms` | Synonyms for a word |
| `pokemon` | Stats for a Pokemon |
| `advice` | A random piece of advice |
| `github_repo` | Stars/forks/language for a repo |
| `trivia` | A random trivia question + answer |
| `programming_joke` | A programming joke |
| `recipe` | Find a recipe by dish name |
| `cocktail` | A cocktail recipe by name |
| `anime` | Look up an anime (MyAnimeList) |
| `quote` | A random inspirational quote |
| `uuid_gen` | Generate UUIDs (offline) |
| `password_gen` | Generate a strong password (offline) |
| `base64_tool` | Base64 encode/decode (offline) |
| `hash_text` | md5/sha256 a string (offline) |
| `lorem_ipsum` | Placeholder text (offline) |
| `json_format` | Validate + pretty-print JSON (offline) |
| `timestamp` | Unix <-> ISO time (offline) |
| `url_check` | HTTP status & timing of a URL |
| `dns_lookup` | Resolve DNS records |
| `chuck_norris` | A Chuck Norris joke |
| `age_guess` | Predict age from a name |
| `dog_image` | A random dog photo |
| `book_search` | Find a book (Open Library) |
| `spacex_latest` | Most recent SpaceX launch |
| `text_stats` | Word/char counts + reading time (offline) |
| `slugify` | Text → URL slug (offline) |
| `case_convert` | snake/camel/kebab/pascal/title (offline) |
| `diff_text` | Unified diff of two texts (offline) |
| `github_releases` | Latest release of a repo |
| `rss_feed` | Latest items from an RSS/Atom feed |
| `regex_test` | Test a regex against text (offline) |
| `jwt_decode` | Decode a JWT header/payload (offline) |
| `url_encode` | URL encode/decode (offline) |
| `roman_numeral` | Int <-> Roman numeral (offline) |
| `geo_distance` | Distance between two coordinates (offline) |
| `loan_payment` | Loan/mortgage monthly payment (offline) |
| `tip_split` | Tip + split a bill (offline) |
| `bmi` | Body mass index (offline) |
| `dice` | Roll dice in NdM notation (offline) |
| `morse_code` | Encode/decode Morse code (offline) |
| `random_color` | Random hex colors (offline) |
| `weather_forecast` | Multi-day forecast for a city |
| `geocode` | Place name → coordinates |
| `random_fact` | A random interesting fact |
| `base_convert` | Number base conversion 2-36 (offline) |
| `days_between` | Days between two dates (offline) |
| `age_calculator` | Age from a birthdate (offline) |
| `percent_change` | Percentage change (offline) |
| `word_frequency` | Most common words in text (offline) |
| `compound_interest` | Investment growth (offline) |
| `reddit_top` | Top posts in a subreddit |
| `crypto_market` | Market cap & rank for a coin |
| `prime_check` | Primality + prime factors (offline) |
| `fibonacci` | Fibonacci sequence (offline) |
| `caesar_cipher` | ROT-N shift cipher (offline) |
| `anagram_check` | Are two words anagrams? (offline) |
| `public_ip` | Your public IP address |
| `wikipedia_random` | A random Wikipedia article |
| `luhn_check` | Validate a card number (Luhn, offline) |
| `day_of_week` | Weekday for a date (offline) |
| `leap_year` | Is a year a leap year? (offline) |
| `scrabble_score` | Scrabble points for a word (offline) |
| `hex_rgb` | Convert hex <-> RGB color (offline) |
| `gravatar` | Gravatar URL for an email (offline) |
| `cat_fact` | A random cat fact |
| `nasa_apod` | NASA picture of the day |
| `password_strength` | Rate a password (offline) |
| `binary_text` | Text <-> binary (offline) |
| `color_contrast` | WCAG contrast of two colors (offline) |
| `nato_alphabet` | Spell text in NATO phonetics (offline) |
| `factorial` | n! factorial (offline) |
| `ascii_code` | Chars <-> code points (offline) |
| `urban_dictionary` | Slang definitions |
| `random_meal` | A random recipe idea |
| `reverse_text` | Reverse a string (offline) |
| `reverse_words` | Reverse word order (offline) |
| `count_vowels` | Count vowels & consonants (offline) |
| `acronym` | Make an acronym from a phrase (offline) |
| `pig_latin` | Translate to Pig Latin (offline) |
| `leetspeak` | Convert to leetspeak (offline) |
| `atbash_cipher` | Atbash cipher (offline) |
| `rot47` | ROT47 cipher (offline) |
| `vigenere_cipher` | Vigenere cipher (offline) |
| `sort_lines` | Sort lines of text (offline) |
| `dedupe_lines` | Remove duplicate lines (offline) |
| `find_replace` | Find and replace text (offline) |
| `word_wrap` | Wrap text to a width (offline) |
| `html_escape` | HTML escape/unescape (offline) |
| `hex_encode` | Text <-> hex (offline) |
| `char_frequency` | Character frequency (offline) |
| `extract_emails` | Extract emails from text (offline) |
| `extract_urls` | Extract URLs from text (offline) |
| `title_case` | Smart title-case (offline) |
| `gcd_lcm` | GCD & LCM of two numbers (offline) |
| `sum_digits` | Sum the digits of a number (offline) |
| `ordinal` | Ordinal of a number, 1->1st (offline) |
| `number_format` | Add thousands separators (offline) |
| `stats_summary` | Mean/median/min/max of numbers (offline) |
| `round_to` | Round to N decimals (offline) |
| `clamp` | Clamp a value to a range (offline) |
| `collatz` | Collatz sequence length (offline) |
| `quadratic` | Solve a quadratic equation (offline) |
| `circle` | Circle area & circumference (offline) |
| `pythagorean` | Hypotenuse of a right triangle (offline) |
| `discount` | Apply a discount to a price (offline) |
| `simple_interest` | Simple interest (offline) |
| `bytes_human` | Humanize a byte count (offline) |
| `seconds_human` | Humanize a duration (offline) |
| `percent_of` | What percent is X of Y (offline) |
| `bit_ops` | Bitwise AND/OR/XOR (offline) |
| `temperature` | Convert a temperature (offline) |
| `is_perfect` | Perfect-number check (offline) |
| `add_days` | Add/subtract days from a date (offline) |
| `is_weekend` | Is a date a weekend? (offline) |
| `days_in_month` | Days in a month (offline) |
| `week_number` | ISO week number of a date (offline) |
| `mime_type` | Guess a file MIME type (offline) |
| `parse_url` | Break a URL into parts (offline) |
| `json_minify` | Minify JSON (offline) |
| `csv_to_json` | Convert CSV to JSON (offline) |
| `semver_compare` | Compare two semver versions (offline) |
| `crc32` | CRC32 checksum of text (offline) |
| `random_string` | Random string generator (offline) |
| `random_int` | Random integer in a range (offline) |
| `coin_flip` | Flip a coin (offline) |
| `magic_8_ball` | Ask the Magic 8-Ball (offline) |
| `random_choice` | Pick a random option (offline) |
| `palindrome` | Palindrome check (offline) |
| `percentage_bar` | ASCII progress bar (offline) |
| `count_substring` | Count occurrences of a substring (offline) |
| `unix_now` | Current unix timestamp (offline) |
| `dedupe_list` | Dedupe a comma list (offline) |
| `levenshtein` | Edit distance between two strings (offline) |
| `jaccard_similarity` | Word-set similarity (offline) |
| `hamming_distance` | Hamming distance (offline) |
| `shannon_entropy` | Shannon entropy of text (offline) |
| `soundex` | Soundex phonetic code (offline) |
| `validate_email` | Validate an email address (offline) |
| `validate_ipv4` | Validate an IPv4 address (offline) |
| `credit_card_type` | Detect a card network (offline) |
| `initials` | Initials from a name (offline) |
| `password_entropy` | Estimate password entropy (offline) |
| `genderize` | Guess gender from a name |
| `nationalize` | Guess nationality from a name |
| `kanye_quote` | A random Kanye quote |
| `yes_no` | Random yes/no with a gif |
| `fox_image` | A random fox photo |
| `word_count_lines` | Words per line (offline) |
| `caesar_brute` | Brute-force a Caesar cipher (offline) |
| `snake_to_words` | snake_case/camelCase -> words (offline) |
| `roman_today` | Decimal to Roman numerals, clock style (offline) |
| `vowel_remove` | Remove vowels from text (offline) |
| `alternating_case` | mOcKiNg sPoNgEbOb case (offline) |
| `clap_text` | Put 👏 between 👏 words (offline) |
| `reverse_each_word` | Reverse letters in each word (offline) |
| `is_isogram` | Isogram check (no repeated letters, offline) |
| `is_pangram` | Pangram check (offline) |
| `number_to_words` | Spell a number in English (offline) |
| `tally` | Count items in a comma list (offline) |
| `longest_word` | Longest & shortest word (offline) |
| `count_syllables` | Estimate syllables in a word (offline) |
| `unique_chars` | Distinct characters (offline) |
| `remove_punctuation` | Strip punctuation (offline) |
| `swapcase` | Swap upper/lower case (offline) |
| `capitalize_sentences` | Capitalize each sentence (offline) |
| `truncate_text` | Truncate with ellipsis (offline) |
| `repeat_text` | Repeat text N times (offline) |
| `count_words` | Word & character count (offline) |
| `frequency_sort` | Sort words by frequency (offline) |
| `caesar_rot13` | ROT13 quick (offline) |
| `cat_image` | A random cat photo |
| `dog_breeds` | List dog breeds |
| `random_joke` | A random joke (setup/punchline) |
| `trivia_categories` | List trivia categories |

Most have short aliases (`fx`→currency, `btc`→crypto, `wiki`→wikipedia,
`b64`→base64_tool, `qr`→qr_code, `repo`→github_repo, …) — `ronin plugin search`
finds them.

---

## Which route should I use?

- The tool already has an **MCP server** (GitHub, Slack, Postgres, Notion…) → MCP.
- It's a **hosted** MCP endpoint (Linear, Atlassian…) → `mcp add-remote`.
- It's just an **API or some logic** and you want it fast → a plugin
  (`ronin plugin new`).
- You want it **today, no setup** → `ronin plugin add` from the 200 built-ins.
