# fetcher

Pulls feeds over HTTP with a small retry loop. The transport is injected, so
nothing here opens a socket by itself.

```python
from fetcher import fetch, from_env

result = fetch("https://example.invalid/feed.xml", transport, from_env())
```

## Configuration

Every field of `FetchConfig` can be overridden from the environment.

| Field | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `timeout_seconds` | `FETCH_TIMEOUT` | `30` | Seconds to wait for a response before giving up on an attempt. Chosen to match the upstream CDN's own 30s edge timeout. |
| `max_retries` | `FETCH_MAX_RETRIES` | `5` | Extra attempts after the first one. Transient 5xx responses and transport errors are retried; everything else is returned to the caller. |
| `follow_redirects` | `FETCH_FOLLOW_REDIRECTS` | `false` | Off by default — feed URLs that redirect are usually stale and we would rather see the 301 than silently follow it. Turn it on per-feed if you trust the publisher. |

Booleans accept `1/true/yes/on` and `0/false/no/off`.

## Support

`fetcher.describe` holds the summaries our support team pastes into tickets.
