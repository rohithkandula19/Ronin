"""Text-to-speech for ronin — completes the media trio (image / video / audio).

Uses the OS's built-in speech engine, so it's free and needs no API key:

- macOS: the ``say`` command (speak aloud, or ``-o file`` to save audio).
- Linux: ``espeak`` / ``espeak-ng`` if installed.

``ronin say "..."`` speaks aloud; ``--out clip.m4a`` saves an audio file
instead (macOS picks the format from the extension: .aiff/.m4a/.wav/.caf).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def tts_engine() -> str | None:
    """Return the available TTS engine name, or None."""
    if sys.platform == "darwin" and shutil.which("say"):
        return "say"
    for eng in ("espeak-ng", "espeak"):
        if shutil.which(eng):
            return eng
    return None


def list_voices() -> list[str]:
    """List installed voice names (macOS ``say -v ?``)."""
    if sys.platform != "darwin" or not shutil.which("say"):
        return []
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    voices: list[str] = []
    for line in out.stdout.splitlines():
        # "Alex                en_US    # Most people recognize me by my voice."
        name = line.split("  ", 1)[0].strip()
        if name:
            voices.append(name)
    return voices


def speak(
    text: str,
    *,
    voice: str | None = None,
    rate: int | None = None,
    out: Path | str | None = None,
) -> Path | None:
    """Speak ``text`` aloud, or save it to ``out`` if given. Returns the output
    path when saving, else None. Raises RuntimeError if no engine is available."""
    engine = tts_engine()
    if engine is None:
        raise RuntimeError(
            "no text-to-speech engine found "
            "(macOS has `say` built in; on Linux install espeak-ng)."
        )

    if engine == "say":
        cmd = ["say"]
        if voice:
            cmd += ["-v", voice]
        if rate:
            cmd += ["-r", str(rate)]
        if out is not None:
            cmd += ["-o", str(out)]
        cmd.append(text)
    else:  # espeak / espeak-ng
        cmd = [engine]
        if voice:
            cmd += ["-v", voice]
        if rate:
            cmd += ["-s", str(rate)]
        if out is not None:
            cmd += ["-w", str(out)]  # espeak writes WAV
        cmd.append(text)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"text-to-speech failed: {e}") from e
    if res.returncode != 0:
        raise RuntimeError(f"text-to-speech failed: {res.stderr[-200:]}")

    return Path(out) if out is not None else None


# --------------------------------------------------------------------------
# Voice input — record from the mic (ffmpeg) + transcribe (Whisper API).
# Transcription uses the provider's OpenAI-compatible /audio/transcriptions
# endpoint; Groq serves Whisper free, OpenAI has whisper-1. Providers without
# a Whisper endpoint (Cerebras/Gemini) raise a clear error.
# --------------------------------------------------------------------------
def record_audio(seconds: float, out_path: Path | str) -> Path:
    """Record ``seconds`` of mono 16 kHz audio from the default mic via ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to record audio (brew install ffmpeg).")
    if sys.platform == "darwin":
        fmt, src = "avfoundation", ":default"
    else:
        fmt, src = "pulse", "default"
    cmd = ["ffmpeg", "-y", "-f", fmt, "-i", src, "-t", str(seconds),
           "-ar", "16000", "-ac", "1", str(out_path)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 20)
    if res.returncode != 0 or not Path(out_path).is_file():
        raise RuntimeError(f"recording failed (mic permission?): {res.stderr[-200:]}")
    return Path(out_path)


def transcribe(audio_path: Path | str, config, *, model: str | None = None) -> str:
    """Transcribe an audio file via the provider's Whisper endpoint."""
    import os

    import httpx
    base = (config.resolved_base_url() or "https://api.openai.com/v1").rstrip("/")
    key = config.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("voice transcription needs an API key (Groq serves Whisper free).")
    if model is None:
        model = "whisper-large-v3-turbo" if "groq" in base else "whisper-1"
    with open(audio_path, "rb") as f:
        r = httpx.post(
            f"{base}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}", "User-Agent": "ronin"},
            files={"file": (Path(audio_path).name, f, "audio/wav")},
            data={"model": model}, timeout=120,
        )
    if r.status_code == 404:
        raise RuntimeError(f"{config.provider} has no Whisper endpoint — use Groq "
                           "(free) or OpenAI for voice: /login groq")
    r.raise_for_status()
    return (r.json().get("text") or "").strip()


def listen(config, *, seconds: float = 6.0, console=None) -> str:
    """Record from the mic and return the transcript."""
    import tempfile
    tmp = Path(tempfile.mktemp(suffix=".wav"))
    try:
        if console:
            console.print(f"[#6b7089]🎙  recording {seconds:.0f}s — speak now…[/#6b7089]")
        record_audio(seconds, tmp)
        if console:
            console.print("[#6b7089]   transcribing…[/#6b7089]")
        return transcribe(tmp, config)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
