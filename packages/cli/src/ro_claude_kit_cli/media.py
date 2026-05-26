"""Image (and video-frame) generation for ronin — terminal-native.

Backends are pluggable; the default is **Pollinations**, which is free and
needs no API key, so ``ronin image "..."`` works out of the box. ``openai`` is
available when you want higher quality (set ``OPENAI_API_KEY``).

Generated images are saved to disk and then *shown in the terminal*:
- iTerm2 → inline image protocol (real picture in the terminal),
- else if ``chafa`` / ``viu`` / ``imgcat`` is installed → ANSI render,
- else → opened in the system image viewer.

HTTP uses stdlib ``urllib`` so there's no extra dependency.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

IMAGE_BACKENDS = ("pollinations", "openai")
_POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"
_USER_AGENT = "ronin/0.3 (+https://github.com/rohithkandula19/RO-Claude-kit)"


@dataclass
class ImageResult:
    path: Path
    backend: str
    prompt: str
    shown_via: str = "none"  # how it was displayed: iterm2 | chafa | viu | imgcat | open | none


# --------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# --------------------------------------------------------------------------
def _http_get(url: str, timeout: float = 180.0) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted hosts)
        return resp.read(), resp.headers.get("Content-Type", "")


def _http_post_json(url: str, body: dict, headers: dict, timeout: float = 180.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read())


# --------------------------------------------------------------------------
# Backends → raw image bytes
# --------------------------------------------------------------------------
def _pollinations_bytes(prompt: str, *, width: int, height: int, seed: int | None,
                        model: str | None) -> tuple[bytes, str]:
    params = {"width": width, "height": height, "nologo": "true"}
    if seed is not None:
        params["seed"] = seed
    if model:
        params["model"] = model
    url = _POLLINATIONS_URL + urllib.parse.quote(prompt, safe="") + "?" + urllib.parse.urlencode(params)
    return _http_get(url)


def _openai_bytes(prompt: str, *, width: int, height: int, model: str | None,
                  api_key: str | None) -> tuple[bytes, str]:
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("openai backend needs OPENAI_API_KEY (set it in your shell).")
    size = f"{width}x{height}"
    body = {"model": model or "gpt-image-1", "prompt": prompt, "n": 1, "size": size}
    data = _http_post_json(
        "https://api.openai.com/v1/images/generations",
        body, {"Authorization": f"Bearer {key}"},
    )
    item = data["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), "image/png"
    if item.get("url"):  # dall-e-3 path
        return _http_get(item["url"])
    raise RuntimeError("openai image response had neither b64_json nor url")


def _ext_for(content_type: str) -> str:
    if "png" in content_type:
        return ".png"
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "webp" in content_type:
        return ".webp"
    return ".png"


def generate_image(
    prompt: str,
    *,
    backend: str = "pollinations",
    out: Path | str | None = None,
    width: int = 1024,
    height: int = 1024,
    seed: int | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> Path:
    """Generate an image and write it to disk; returns the file path."""
    if backend == "pollinations":
        raw, ctype = _pollinations_bytes(prompt, width=width, height=height, seed=seed, model=model)
    elif backend == "openai":
        raw, ctype = _openai_bytes(prompt, width=width, height=height, model=model, api_key=api_key)
    else:
        raise ValueError(f"unknown image backend '{backend}' (choose: {', '.join(IMAGE_BACKENDS)})")

    if not raw:
        raise RuntimeError("image backend returned no data")

    if out is None:
        out = Path.cwd() / f"ronin_image_{int(time.time())}{_ext_for(ctype)}"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return out


# --------------------------------------------------------------------------
# Terminal display
# --------------------------------------------------------------------------
def _iterm2_inline(path: Path) -> bool:
    """Emit the iTerm2 inline-image escape sequence. Returns True if attempted."""
    if os.environ.get("TERM_PROGRAM") != "iTerm.app":
        return False
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    name_b64 = base64.b64encode(path.name.encode()).decode("ascii")
    seq = (
        f"\033]1337;File=name={name_b64};size={len(data)};"
        f"inline=1;width=auto;height=auto;preserveAspectRatio=1:{b64}\a\n"
    )
    sys.stdout.write(seq)
    sys.stdout.flush()
    return True


def _render_with_tool(path: Path) -> str | None:
    """Render via chafa / viu / imgcat if available. Returns the tool name used."""
    for tool, args in (
        ("chafa", ["--size", "60x30", str(path)]),
        ("viu", ["-w", "60", str(path)]),
        ("imgcat", [str(path)]),
    ):
        if shutil.which(tool):
            try:
                subprocess.run([tool, *args], check=False, timeout=30)
                return tool
            except (OSError, subprocess.SubprocessError):
                continue
    return None


def _open_in_viewer(path: Path) -> bool:
    opener = "open" if sys.platform == "darwin" else ("start" if os.name == "nt" else "xdg-open")
    try:
        if opener == "start":
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        else:
            subprocess.run([opener, str(path)], check=False, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError, AttributeError):
        return False


def display_image(path: Path) -> str:
    """Show ``path`` in the terminal by the best available means. Returns the
    method used ('iterm2' | 'chafa' | 'viu' | 'imgcat' | 'open' | 'none')."""
    if _iterm2_inline(path):
        return "iterm2"
    tool = _render_with_tool(path)
    if tool:
        return tool
    if _open_in_viewer(path):
        return "open"
    return "none"
