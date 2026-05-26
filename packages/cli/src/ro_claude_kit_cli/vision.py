"""``ronin see <image> "<question>"`` — vision. Ask Claude (or any vision-capable
model) about a local image. The mirror of ``ronin image``: ronin can now both
*generate* and *understand* pictures.

A one-shot multimodal call (not the tool loop): read the image, base64-encode it,
send it with the question, print the answer. Works with Anthropic (Claude) and
OpenAI-compatible vision models (e.g. gpt-4o, llama vision); text-only models
will refuse — use a vision model.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

from .config import CSKConfig

_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}
DEFAULT_QUESTION = "Describe this image in detail."


def _media_type(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "image/png")


def describe_image(config: CSKConfig, image_path: Path | str, question: str | None = None) -> str:
    """Return the model's answer about ``image_path``. Raises on bad input/auth."""
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"no such image: {path}")
    q = (question or DEFAULT_QUESTION).strip()
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    media_type = _media_type(path)

    if config.provider == "anthropic":
        return _anthropic_vision(config, b64, media_type, q)
    return _openai_vision(config, b64, media_type, q)


def _anthropic_vision(config: CSKConfig, b64: str, media_type: str, question: str) -> str:
    import anthropic

    key = config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    resp = client.messages.create(
        model=config.resolved_model(),
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": question},
            ],
        }],
    )
    return "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def _openai_vision(config: CSKConfig, b64: str, media_type: str, question: str) -> str:
    key = config.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(f"{config.provider} needs an API key (set it with `ronin set-key`).")
    base = config.resolved_base_url() or "https://api.openai.com/v1"
    body = {
        "model": config.resolved_model(),
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
            ],
        }],
    }
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "ronin (+https://github.com/rohithkandula19/Ronin)",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        out = json.loads(resp.read())
    return (out["choices"][0]["message"].get("content") or "").strip()
