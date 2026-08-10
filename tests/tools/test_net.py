"""web_fetch and web_search, with the network and the fast model injected.

No socket is opened. The point under test is the *shape* of the tool — that it returns
an extraction rather than the page, that it caches, and that it refuses to fetch this
machine — none of which needs a real HTTP request to verify.
"""

from __future__ import annotations

from pathlib import Path

from harness import FakeNet, call, context

from ronin.tools import WebFetchTool, WebSearchTool, html_to_markdown
from ronin.tools.net import CACHE_TTL_SECONDS, MAX_MARKDOWN_CHARS

PAGE = (
    "<html><head><title>Doc</title><style>body{color:red}</style>"
    "<script>track()</script></head><body>"
    "<h1>Changelog</h1><p>Version 3.0 removed the sync client.</p>"
    "<ul><li>Renamed timeout</li><li>Dropped py3.8</li></ul>"
    "</body></html>"
)


def fetcher(net: FakeNet) -> WebFetchTool:
    return WebFetchTool(fetch=net.fetch, extract=net.extract, clock=net.clock)


# --------------------------------------------------------------------------- #
# HTML reduction
# --------------------------------------------------------------------------- #


def test_scripts_and_styles_are_removed() -> None:
    markdown = html_to_markdown(PAGE)
    assert "track()" not in markdown
    assert "color:red" not in markdown


def test_headings_and_lists_survive_because_they_locate_the_answer() -> None:
    markdown = html_to_markdown(PAGE)
    assert "# Changelog" in markdown
    assert "- Renamed timeout" in markdown
    assert "- Dropped py3.8" in markdown


def test_tags_are_stripped_and_entities_decoded() -> None:
    markdown = html_to_markdown("<p>a &amp; b &nbsp;&lt;tag&gt;</p>")
    assert "<p>" not in markdown
    assert "a & b" in markdown
    assert "<tag>" in markdown


def test_blank_runs_are_collapsed() -> None:
    assert "\n\n\n" not in html_to_markdown("<p>a</p><p>b</p><p>c</p>")


def test_comments_are_removed() -> None:
    assert "secret" not in html_to_markdown("<p>a</p><!-- secret --><p>b</p>")


# --------------------------------------------------------------------------- #
# web_fetch
# --------------------------------------------------------------------------- #


async def test_the_answer_comes_back_not_the_page(tmp_path: Path) -> None:
    net = FakeNet({"https://x.test/CHANGELOG": ("text/html", PAGE)}, answer="3.0 removed sync.")
    result = await call(
        fetcher(net),
        context(tmp_path),
        url="https://x.test/CHANGELOG",
        prompt="What changed in 3.0?",
    )
    assert result.ok
    assert "3.0 removed sync." in result.content
    assert "<html>" not in result.content
    assert "Renamed timeout" not in result.content, "the page itself must not come back"


async def test_the_result_says_where_it_came_from(tmp_path: Path) -> None:
    """A summary can be wrong; the caller needs to know it is a summary of what."""
    net = FakeNet({"https://x.test/a": ("text/html", PAGE)})
    result = await call(fetcher(net), context(tmp_path), url="https://x.test/a", prompt="q")
    assert "https://x.test/a" in result.content
    assert "extracted from" in result.content


async def test_the_extractor_gets_the_markdown_and_the_prompt(tmp_path: Path) -> None:
    net = FakeNet({"https://x.test/a": ("text/html", PAGE)})
    await call(fetcher(net), context(tmp_path), url="https://x.test/a", prompt="the question")
    prompt, text = net.extractions[0]
    assert prompt == "the question"
    assert "# Changelog" in text
    assert "<script>" not in text


async def test_a_second_fetch_within_the_ttl_is_served_from_cache(tmp_path: Path) -> None:
    net = FakeNet({"https://x.test/a": ("text/html", PAGE)})
    tool = fetcher(net)
    ctx = context(tmp_path)
    await call(tool, ctx, url="https://x.test/a", prompt="q")
    net.now += CACHE_TTL_SECONDS - 1
    second = await call(tool, ctx, url="https://x.test/a", prompt="q")
    assert len(net.fetches) == 1, "the page must not be fetched twice"
    assert "from cache" in second.content
    assert len(net.extractions) == 2, "but the question is asked again"


async def test_the_cache_expires_after_the_ttl(tmp_path: Path) -> None:
    net = FakeNet({"https://x.test/a": ("text/html", PAGE)})
    tool = fetcher(net)
    ctx = context(tmp_path)
    await call(tool, ctx, url="https://x.test/a", prompt="q")
    net.now += CACHE_TTL_SECONDS + 1
    await call(tool, ctx, url="https://x.test/a", prompt="q")
    assert len(net.fetches) == 2


async def test_different_urls_are_cached_separately(tmp_path: Path) -> None:
    net = FakeNet(
        {"https://x.test/a": ("text/html", PAGE), "https://x.test/b": ("text/html", PAGE)}
    )
    tool = fetcher(net)
    ctx = context(tmp_path)
    await call(tool, ctx, url="https://x.test/a", prompt="q")
    await call(tool, ctx, url="https://x.test/b", prompt="q")
    assert len(net.fetches) == 2


async def test_a_non_html_body_is_passed_through_unreduced(tmp_path: Path) -> None:
    net = FakeNet({"https://x.test/raw.md": ("text/markdown", "# Title\n\nBody\n")})
    await call(fetcher(net), context(tmp_path), url="https://x.test/raw.md", prompt="q")
    _, text = net.extractions[0]
    assert text == "# Title\n\nBody\n"


async def test_an_enormous_page_is_clipped_before_extraction(tmp_path: Path) -> None:
    body = "<p>" + ("word " * 40_000) + "</p>"
    net = FakeNet({"https://x.test/big": ("text/html", body)})
    result = await call(fetcher(net), context(tmp_path), url="https://x.test/big", prompt="q")
    _, text = net.extractions[0]
    assert len(text) <= MAX_MARKDOWN_CHARS
    assert "page truncated before extraction" in result.content


async def test_an_empty_page_is_reported_rather_than_extracted(tmp_path: Path) -> None:
    net = FakeNet({"https://x.test/js": ("text/html", "<html><script>app()</script></html>")})
    result = await call(fetcher(net), context(tmp_path), url="https://x.test/js", prompt="q")
    assert not result.ok
    assert "no readable text" in result.error
    assert "JavaScript" in result.error


async def test_a_non_http_url_is_refused(tmp_path: Path) -> None:
    net = FakeNet()
    result = await call(fetcher(net), context(tmp_path), url="file:///etc/passwd", prompt="q")
    assert not result.ok
    assert "not an http(s) URL" in result.error
    assert "read tool" in result.error
    assert net.fetches == []


async def test_localhost_is_refused(tmp_path: Path) -> None:
    net = FakeNet()
    for url in (
        "http://localhost:3000/",
        "http://127.0.0.1/",
        "http://0.0.0.0:8080/x",
    ):
        result = await call(fetcher(net), context(tmp_path), url=url, prompt="q")
        assert not result.ok, url
        assert "points at this machine" in result.error
    assert net.fetches == []


async def test_an_empty_prompt_is_refused(tmp_path: Path) -> None:
    net = FakeNet({"https://x.test/a": ("text/html", PAGE)})
    result = await call(fetcher(net), context(tmp_path), url="https://x.test/a", prompt="  ")
    assert not result.ok
    assert "say what to extract" in result.error


def test_the_description_warns_that_the_result_is_an_extraction() -> None:
    net = FakeNet()
    description = fetcher(net).spec().description
    assert "extraction" in description or "verbatim" in description
    assert "web_search" in description, "point at the tool for finding a page"


# --------------------------------------------------------------------------- #
# web_search
# --------------------------------------------------------------------------- #


async def test_search_results_are_numbered_with_urls_and_snippets(tmp_path: Path) -> None:
    net = FakeNet(
        results=[
            {"title": "ripgrep guide", "url": "https://rg.test/guide", "snippet": "--multiline"},
            {"title": "SO answer", "url": "https://so.test/1", "snippet": "use -U"},
        ]
    )
    result = await call(WebSearchTool(search=net.search), context(tmp_path), query="rg multiline")
    assert result.ok
    assert "1. ripgrep guide" in result.content
    assert "https://rg.test/guide" in result.content
    assert "--multiline" in result.content
    assert "2 results" in result.content


async def test_no_results_suggests_rephrasing(tmp_path: Path) -> None:
    net = FakeNet(results=[])
    result = await call(WebSearchTool(search=net.search), context(tmp_path), query="zzz")
    assert result.ok
    assert "no results" in result.content
    assert "exact error message" in result.content


async def test_an_empty_query_is_refused(tmp_path: Path) -> None:
    net = FakeNet()
    result = await call(WebSearchTool(search=net.search), context(tmp_path), query="   ")
    assert not result.ok
    assert net.searches == []


async def test_a_missing_snippet_does_not_break_rendering(tmp_path: Path) -> None:
    net = FakeNet(results=[{"title": "T", "url": "https://x.test"}])
    result = await call(WebSearchTool(search=net.search), context(tmp_path), query="q")
    assert result.ok
    assert "T" in result.content


def test_the_search_description_points_at_grep_for_local_code() -> None:
    net = FakeNet()
    description = WebSearchTool(search=net.search).spec().description
    assert "grep" in description
    assert "snippets" in description.lower()
