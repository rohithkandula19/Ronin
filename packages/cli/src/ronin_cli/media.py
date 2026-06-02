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
_USER_AGENT = "ronin/0.3 (+https://github.com/rohithkandula19/Ronin)"


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


def _http_get_json(url: str, headers: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, headers={**headers, "User-Agent": _USER_AGENT})
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


def _image_bytes(prompt: str, backend: str, width: int, height: int,
                 seed: int | None, model: str | None, api_key: str | None) -> tuple[bytes, str]:
    """Dispatch to the chosen backend → (raw bytes, content-type)."""
    if backend == "pollinations":
        return _pollinations_bytes(prompt, width=width, height=height, seed=seed, model=model)
    if backend == "openai":
        return _openai_bytes(prompt, width=width, height=height, model=model, api_key=api_key)
    raise ValueError(f"unknown image backend '{backend}' (choose: {', '.join(IMAGE_BACKENDS)})")


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
    raw, ctype = _image_bytes(prompt, backend, width, height, seed, model, api_key)

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


def open_file(path: Path) -> bool:
    """Open any file (e.g. an mp4) in the system default app."""
    return _open_in_viewer(path)


# --------------------------------------------------------------------------
# Video: free path = generate frames, stitch with ffmpeg into a real mp4.
# True real-motion text-to-video needs a paid provider (Replicate/Runway/fal);
# the ``backend`` here is the per-frame image backend, kept pluggable so a
# motion provider can slot in later.
# --------------------------------------------------------------------------
@dataclass
class VideoResult:
    path: Path
    poster: Path | None
    frames: int
    fps: int


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# --------------------------------------------------------------------------
# Real-motion video via Replicate (paid). Generates an actual motion clip
# (not frame-animation). Needs REPLICATE_API_TOKEN; costs money per clip.
# --------------------------------------------------------------------------
DEFAULT_REPLICATE_VIDEO_MODEL = "minimax/video-01"
_REPLICATE_API = "https://api.replicate.com/v1"


def generate_video_replicate(
    prompt: str,
    *,
    out: Path | str | None = None,
    model: str = DEFAULT_REPLICATE_VIDEO_MODEL,
    api_key: str | None = None,
    poll_timeout: float = 600.0,
    on_status=None,  # callback(status:str)
) -> VideoResult:
    """Generate a real-motion clip on Replicate and download the mp4.

    ``model`` is an ``owner/name`` slug (runs its latest version). Returns the
    saved mp4 (no poster). Raises RuntimeError on missing key / failure / timeout.
    """
    key = api_key or os.environ.get("REPLICATE_API_TOKEN")
    if not key:
        raise RuntimeError("replicate engine needs REPLICATE_API_TOKEN (set it in your shell).")
    headers = {"Authorization": f"Bearer {key}"}

    pred = _http_post_json(
        f"{_REPLICATE_API}/models/{model}/predictions",
        {"input": {"prompt": prompt}}, headers,
    )
    get_url = (pred.get("urls") or {}).get("get") or f"{_REPLICATE_API}/predictions/{pred.get('id')}"
    status = pred.get("status", "starting")
    if on_status:
        on_status(status)

    start = time.time()
    while status not in ("succeeded", "failed", "canceled"):
        if time.time() - start > poll_timeout:
            raise RuntimeError(f"replicate timed out after {int(poll_timeout)}s (status={status})")
        time.sleep(2.0)
        pred = _http_get_json(get_url, headers)
        new_status = pred.get("status", status)
        if on_status and new_status != status:
            on_status(new_status)
        status = new_status

    if status != "succeeded":
        raise RuntimeError(f"replicate prediction {status}: {pred.get('error') or 'no detail'}")

    output = pred.get("output")
    url = output[-1] if isinstance(output, list) and output else output
    if not isinstance(url, str):
        raise RuntimeError("replicate succeeded but returned no video URL")
    raw, _ = _http_get(url)

    if out is None:
        out = Path.cwd() / f"ronin_video_{int(time.time())}.mp4"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return VideoResult(path=out, poster=None, frames=0, fps=0)


# --------------------------------------------------------------------------
# Conversational media tools — wired into the `ronin` chat so a single natural-
# language request ("generate me a naruto image") routes to the right action.
# Each tool produces a file and records it in ``artifacts`` so the chat loop can
# show it (image inline, video opened) after the turn; speech plays immediately.
# --------------------------------------------------------------------------
def build_media_tools(artifacts: list, *, root: Path | str = ".", image_backend: str = "pollinations"):
    """Return [generate_image, generate_video, speak] tools for the chat agent.

    ``artifacts`` is a list the tools append ``(kind, Path)`` to; the caller
    displays them after the agent turn (avoids clashing with a live spinner).
    """
    from ronin_agent_patterns import Tool

    from .audio import speak as _speak
    from .audio import tts_engine

    root_path = Path(root).resolve()

    def _gen_image(prompt: str, filename: str | None = None) -> str:
        out = root_path / filename if filename else None
        try:
            path = generate_image(prompt, backend=image_backend, out=out)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: image generation failed: {e}"
        artifacts.append(("image", path))
        return f"Image generated → {path.name}. It is being shown to the user now. Briefly confirm."

    def _gen_video(prompt: str, frames: int = 12) -> str:
        if not ffmpeg_available():
            return "ERROR: ffmpeg isn't installed; can't make a video. Tell the user to run `brew install ffmpeg`."
        try:
            result = generate_video(prompt, frames=max(2, min(int(frames), 24)))
        except Exception as e:  # noqa: BLE001
            return f"ERROR: video generation failed: {e}"
        artifacts.append(("video", result.path))
        return f"Video generated → {result.path.name} ({result.frames} frames). Opening it for the user. Briefly confirm."

    def _say(text: str, voice: str | None = None) -> str:
        if tts_engine() is None:
            return "ERROR: no text-to-speech engine available on this machine."
        try:
            _speak(text, voice=voice)
        except RuntimeError as e:
            return f"ERROR: {e}"
        return "Spoke it aloud to the user. Briefly confirm."

    return [
        Tool(
            name="generate_image",
            description="Generate an image from a text prompt (free). Use whenever the user asks to draw/create/make a picture, logo, art, etc. Args: prompt; optional filename.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed description of the image."},
                    "filename": {"type": "string", "description": "Optional save name, e.g. naruto.png."},
                },
                "required": ["prompt"],
            },
            handler=_gen_image,
        ),
        Tool(
            name="generate_video",
            description="Generate a short video from a text prompt (free, frame-animation). Use when the user asks for a video/animation/clip. Args: prompt; optional frames (2-24).",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "frames": {"type": "integer", "description": "How many frames (2-24)."},
                },
                "required": ["prompt"],
            },
            handler=_gen_video,
        ),
        Tool(
            name="speak",
            description="Speak text aloud via text-to-speech (free). Use when the user asks you to say/read/voice something. Args: text; optional voice name.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string"},
                },
                "required": ["text"],
            },
            handler=_say,
        ),
    ]


def show_artifacts(artifacts: list) -> None:
    """Display produced media: images inline, videos opened. Clears the list."""
    for kind, path in artifacts:
        try:
            if kind == "image":
                display_image(Path(path))
            elif kind == "video":
                open_file(Path(path))
        except Exception:  # noqa: BLE001 — display is best-effort
            pass
    artifacts.clear()


# --------------------------------------------------------------------------
# Agent tool: let `ronin code` / `ronin agent` generate images mid-task.
# --------------------------------------------------------------------------
def build_image_tool(root: Path | str, *, backend: str = "pollinations"):
    """A Tool the coding agent can call to create an image and save it into the
    project (e.g. "design a logo and save it to assets/logo.png")."""
    from ronin_agent_patterns import Tool

    root_path = Path(root).resolve()

    def _generate(prompt: str, path: str, width: int = 1024, height: int = 1024) -> str:
        target = (root_path / path).resolve()
        # refuse path traversal outside the project root
        if root_path != target and root_path not in target.parents:
            return f"ERROR: refusing to write outside the project root: {path}"
        try:
            out = generate_image(prompt, backend=backend, out=target, width=width, height=height)
        except Exception as e:  # noqa: BLE001 — return the error to the agent to reason about
            return f"ERROR: image generation failed: {e}"
        rel = out.relative_to(root_path) if root_path in out.parents else out
        return f"saved generated image to {rel} ({out.stat().st_size} bytes)"

    return Tool(
        name="generate_image",
        description=(
            "Generate an image from a text prompt and save it into the project. "
            "Use for logos, diagrams, illustrations, placeholder art. "
            "Args: prompt (what to draw), path (where to save, relative to the "
            "project root, e.g. 'assets/logo.png'), optional width/height."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "path": {"type": "string", "description": "Save path relative to the project root."},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
            },
            "required": ["prompt", "path"],
        },
        handler=_generate,
    )


def generate_video(
    prompt: str,
    *,
    out: Path | str | None = None,
    frames: int = 12,
    fps: int = 8,
    width: int = 512,
    height: int = 512,
    seed: int | None = None,
    backend: str = "pollinations",
    model: str | None = None,
    api_key: str | None = None,
    on_frame=None,  # callback(done:int, total:int)
) -> VideoResult:
    """Generate ``frames`` AI images (incrementing the seed) and stitch them
    into an mp4 with ffmpeg. Returns the mp4 path + a poster (first frame).

    This is frame-animation, not real-motion text-to-video — the honest free
    way to produce an actual video file. Swap in a paid motion provider for
    Sora-grade results.
    """
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg not found — install it to stitch frames into a video "
            "(macOS: `brew install ffmpeg`)."
        )
    if frames < 2:
        raise ValueError("need at least 2 frames for a video")
    # libx264 + yuv420p require even dimensions.
    width -= width % 2
    height -= height % 2
    base_seed = seed if seed is not None else 1000

    import tempfile

    poster_path: Path | None = None
    frame_ext = ".png"  # set from the first frame's real content-type
    with tempfile.TemporaryDirectory(prefix="ronin_frames_") as td:
        tdp = Path(td)
        for i in range(frames):
            raw, ctype = _image_bytes(prompt, backend, width, height, base_seed + i, model, api_key)
            if not raw:
                raise RuntimeError(f"frame {i + 1} came back empty")
            if i == 0:
                # ffmpeg's image2 demuxer picks the decoder by file extension, so
                # the frame files MUST carry the real type (Pollinations → jpeg).
                frame_ext = _ext_for(ctype)
                poster_path = Path(tempfile.gettempdir()) / f"ronin_poster_{int(time.time())}{frame_ext}"
                poster_path.write_bytes(raw)
            (tdp / f"frame_{i:04d}{frame_ext}").write_bytes(raw)
            if on_frame is not None:
                on_frame(i + 1, frames)

        if out is None:
            out = Path.cwd() / f"ronin_video_{int(time.time())}.mp4"
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-framerate", str(fps),
            "-i", str(tdp / f"frame_%04d{frame_ext}"),
            # scale to even dims defensively, then yuv420p for broad player support
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {res.stderr[-400:]}")

    return VideoResult(path=out, poster=poster_path, frames=frames, fps=fps)
