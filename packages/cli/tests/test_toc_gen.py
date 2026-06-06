"""Tests for markdown TOC generation."""
from __future__ import annotations

from ronin_cli.toc_gen import anchor, extract_headings, generate_toc

_MD = '''# Title
intro
## Setup
### Install
```
# this is code, not a heading
```
## Usage & Tips
content
'''


def test_extract_skips_code_blocks() -> None:
    hs = extract_headings(_MD)
    texts = [t for _, t in hs]
    assert texts == ["Title", "Setup", "Install", "Usage & Tips"]
    assert "this is code, not a heading" not in texts


def test_anchor_slug() -> None:
    assert anchor("Usage & Tips") == "usage--tips"
    assert anchor("Hello World") == "hello-world"


def test_generate_toc_nesting() -> None:
    toc = generate_toc(extract_headings(_MD))   # min_level 2 by default
    assert "- [Setup](#setup)" in toc
    assert "  - [Install](#install)" in toc      # nested under Setup
    assert "Title" not in toc                     # h1 skipped at min_level 2
