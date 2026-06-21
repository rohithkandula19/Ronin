"""`ronin local` — make ronin run on a FULLY LOCAL open-source model, ZERO API key.

This is the direct answer to "why do we need an API for everything." ronin already
ships a provider-agnostic ``ollama`` provider (base_url ``http://localhost:11434/v1``,
no key — see ``config.PROVIDER_PRESETS`` and ``runner.build_single_provider``). This
module wraps the last mile: detect/guide an Ollama install, pick + pull a real *coding*
model sized to the machine's RAM, persist ``provider='ollama'`` via ronin's EXISTING
``save_config`` seam, and verify with a tiny real completion through ronin's own
provider layer (the same ``provider.complete`` path ``doctor --check`` uses).

Layered for testability:
  * PURE (unit-tested, no I/O): ``recommend_model``, ``parse_ollama_tags``,
    ``ollama_installed``.
  * IMPURE (real subprocess / network / disk): ``detect_ram_gb``, ``detect_ollama``,
    ``pull_model``, ``apply_local_config``.
  * ``run`` ties it together for the CLI.

stdlib + rich + subprocess only — no extra dependencies, no SDKs.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # rich is a runtime dep; only imported for typing here
    from rich.console import Console


# Where the local Ollama server lives. ronin's 'ollama' preset uses the OpenAI-compat
# shim at .../v1; the native API (tags, pull) is the bare host below.
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_BASE_URL = f"{OLLAMA_HOST}/v1"

# RAM → recommended Ollama *coding* model. We deliberately pick the qwen2.5-coder
# family across the board: it's a strong, currently-available, tool-capable open
# coding model published at exactly these sizes on the Ollama registry, so each tag
# below is a real `ollama pull` target. Bigger RAM → bigger model (more capable,
# more memory). Boundaries are the usable-RAM floors a given size comfortably runs at:
#
#     detected RAM (GB)   model tag                approx download
#     ----------------    --------------------     ---------------
#     < 8                 qwen2.5-coder:1.5b       ~1.0 GB   (low-RAM / laptops)
#     8  ..< 16           qwen2.5-coder:7b         ~4.7 GB   (the common default)
#     16 ..< 32           qwen2.5-coder:14b        ~9   GB
#     >= 32               qwen2.5-coder:32b        ~20  GB   (workstations)
#
# To override the pick, pass `ronin local --model <tag>` (any `ollama pull`-able tag).
RAM_MODEL_TABLE: tuple[tuple[int, str], ...] = (
    (8, "qwen2.5-coder:1.5b"),
    (16, "qwen2.5-coder:7b"),
    (32, "qwen2.5-coder:14b"),
)
_LARGEST_MODEL = "qwen2.5-coder:32b"


# --------------------------------------------------------------------------- #
# PURE functions — no I/O, fully unit-testable.
# --------------------------------------------------------------------------- #
def recommend_model(ram_gb: int) -> str:
    """Pick a real Ollama coding-model tag for a machine with ``ram_gb`` GB of RAM.

    See ``RAM_MODEL_TABLE`` for the documented thresholds. Returns the *largest*
    model whose RAM floor the machine clears:

        <8 → qwen2.5-coder:1.5b · <16 → :7b · <32 → :14b · else → :32b

    Non-positive / nonsensical RAM falls back to the smallest model (safe default
    for an unknown or unreadable machine).
    """
    if ram_gb <= 0:
        return RAM_MODEL_TABLE[0][1]
    for ceiling, tag in RAM_MODEL_TABLE:
        if ram_gb < ceiling:
            return tag
    return _LARGEST_MODEL


def parse_ollama_tags(text: str) -> list[str]:
    """Parse installed model names from EITHER ``ollama list`` table output OR the
    ``/api/tags`` JSON body. Returns names in source order, de-duplicated, with no
    blanks. Unrecognised / empty input yields ``[]`` (never raises).

    - JSON form (``{"models": [{"name": "qwen2.5-coder:7b", ...}, ...]}``): pull the
      ``name`` (or ``model``) of each entry.
    - Table form: skip the ``NAME  ID  SIZE  MODIFIED`` header and take the first
      whitespace-delimited column of each remaining row.
    """
    text = (text or "").strip()
    if not text:
        return []

    names: list[str] = []
    # JSON body from GET /api/tags (or the OpenAI-compat /v1/models {"data":[...]}).
    # A leading {/[ means this is meant to be JSON: if it parses as an object, that
    # result is authoritative (even an EMPTY list → return []); we only fall through
    # to table parsing when it doesn't parse as a JSON object at all (so a malformed
    # body returns [] rather than scraping '{"models":' as a bogus model name).
    if text[0] in "{[":
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            entries = data.get("models")
            if entries is None:
                entries = data.get("data")  # OpenAI-compat shape
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict):
                        name = entry.get("name") or entry.get("model") or entry.get("id")
                        if name:
                            names.append(str(name).strip())
            return _dedupe(names)
        # Not a parseable JSON object — don't table-scrape a JSON-looking line.
        return []

    # `ollama list` table form.
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        first = line.split()[0]
        # Drop the column header (only when it's literally the NAME header row).
        if i == 0 and first.upper() == "NAME":
            continue
        names.append(first)
    return _dedupe(names)


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-dupe, dropping blanks."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def ollama_installed(which_output: str) -> bool:
    """Is the ``ollama`` binary present? ``which_output`` is the result of
    ``shutil.which("ollama")`` (or ``which ollama``): a non-empty path string means
    installed; ``None`` / empty / a 'not found' message means not. Pure so the
    binary-presence branch is testable without a real PATH.
    """
    if not which_output:
        return False
    out = which_output.strip()
    if not out:
        return False
    # `which` on a miss can print "ollama not found" / "no ollama in ..." to stdout.
    low = out.lower()
    if "not found" in low or low.startswith("no ") or "not in" in low:
        return False
    return True


# --------------------------------------------------------------------------- #
# IMPURE functions — real subprocess / network / disk.
# --------------------------------------------------------------------------- #
def detect_ram_gb() -> int:
    """Best-effort total system RAM in GB (floored). Tries POSIX ``sysconf`` first,
    then ``sysctl hw.memsize`` (macOS), then ``/proc/meminfo`` (Linux). Returns ``0``
    if every probe fails — ``recommend_model(0)`` then yields the safe smallest model.
    Never raises.
    """
    # 1) POSIX sysconf — works on Linux and macOS.
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return int((pages * page_size) // (1024**3))
    except (ValueError, OSError, AttributeError):
        pass
    # 2) macOS sysctl.
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(int(out.stdout.strip()) // (1024**3))
    except (OSError, subprocess.SubprocessError):
        pass
    # 3) Linux /proc/meminfo (MemTotal is in kB).
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return int(kb // (1024**2))
    except (OSError, ValueError, IndexError):
        pass
    return 0


def detect_ollama() -> bool:
    """True iff Ollama is BOTH installed (binary on PATH) AND its server is reachable
    on localhost:11434. A binary with no running server returns False (the user still
    needs ``ollama serve``); ``run`` prints the right next step for each case.
    """
    if not ollama_installed(shutil.which("ollama") or ""):
        return False
    return ollama_server_reachable()


def ollama_server_reachable() -> bool:
    """True iff the local Ollama server answers on ``/api/tags``. Short timeout,
    never raises."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=3):  # noqa: S310 — fixed localhost
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def list_installed_models() -> list[str]:
    """Names of models already pulled locally (via the ``/api/tags`` endpoint),
    parsed by the pure :func:`parse_ollama_tags`. ``[]`` if the server is unreachable.
    Lets ``run`` skip a redundant multi-GB pull when the model is already present."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — fixed localhost
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return parse_ollama_tags(body)


def pull_model(model: str, *, console: "Console | None" = None) -> bool:
    """Run ``ollama pull <model>``, streaming its progress to the terminal so the
    multi-GB download isn't a silent hang. Returns True on exit code 0. Never raises
    — a missing binary or a failed pull returns False and is reported by the caller.
    """
    try:
        # No capture: let ollama draw its own progress bars straight to the TTY.
        proc = subprocess.run(["ollama", "pull", model], timeout=60 * 60)
    except FileNotFoundError:
        if console is not None:
            console.print("[red]✗ `ollama` binary not found on PATH.[/red]")
        return False
    except (OSError, subprocess.SubprocessError) as e:
        if console is not None:
            console.print(f"[red]✗ pull failed:[/red] [dim]{e}[/dim]")
        return False
    return proc.returncode == 0


def apply_local_config(model: str, *, scope: str = "user") -> "object":
    """Persist a fully-local config via ronin's EXISTING save path — no new format.

    Loads the current config (so other settings + service keys survive), flips the
    provider trio to local Ollama, and writes it back with ``save_config``:

        provider = "ollama"
        model    = <model>
        base_url = "http://localhost:11434/v1"   (the 'ollama' preset value)

    ``scope`` is ronin's standard "user" (``~/.config/ronin/``) or "project"
    (``./.ronin/``); defaults to "user" so ``ronin local`` makes the machine local
    everywhere, not just one repo. Returns the written path (from ``save_config``).
    """
    from .config import PROVIDER_PRESETS, load_config, save_config

    cfg = load_config()
    cfg.demo_mode = False
    cfg.provider = "ollama"
    cfg.model = model
    cfg.base_url = PROVIDER_PRESETS["ollama"]["base_url"]  # http://localhost:11434/v1
    return save_config(cfg, scope=scope)


def verify_local_completion(model: str) -> tuple[bool, Exception | None]:
    """Send a tiny real completion through ronin's OWN provider layer against the
    local model — the same end-to-end seam ``doctor --check`` uses
    (``build_single_provider`` + ``provider.complete``). Proves the model actually
    answers locally with zero API key. Returns ``(ok, error)``; never raises.
    """
    try:
        from ronin_agent_patterns import Message

        from .config import PROVIDER_PRESETS, RoninConfig
        from .runner import build_single_provider

        cfg = RoninConfig(
            provider="ollama",
            model=model,
            base_url=PROVIDER_PRESETS["ollama"]["base_url"],
        )
        provider = build_single_provider(cfg)
        # Fail fast — a local model that isn't answering shouldn't sit in backoff.
        if hasattr(provider, "max_retries"):
            provider.max_retries = 0
        if hasattr(provider, "timeout"):
            provider.timeout = 120.0  # first local token can be slow while it loads
        provider.complete(
            system="",
            messages=[Message(role="user", content="ping")],
            tools=[],
            max_tokens=1,
        )
        return True, None
    except Exception as e:  # noqa: BLE001 — surfaced as a remedy by run()
        return False, e


# --------------------------------------------------------------------------- #
# Install guidance (printed when Ollama is missing — never crash, just instruct).
# --------------------------------------------------------------------------- #
def _print_install_instructions(console: "Console", *, server_down_only: bool = False) -> None:
    """Print the exact steps to get Ollama running, then let ``run`` stop gracefully.

    ``server_down_only`` → the binary IS installed but the server isn't answering, so
    only the ``ollama serve`` step is needed.
    """
    from rich.panel import Panel

    import platform

    if server_down_only:
        console.print(Panel.fit(
            "Ollama is installed but its server isn't responding on "
            f"[cyan]{OLLAMA_HOST}[/cyan].\n\n"
            "Start it, then re-run [bold]ronin local[/bold]:\n"
            "  [bold]ollama serve[/bold]\n"
            "[dim](on macOS, opening the Ollama app also starts the server)[/dim]",
            title="Start the Ollama server", border_style="yellow",
        ))
        return

    is_mac = platform.system() == "Darwin"
    install_line = (
        "  [bold]brew install ollama[/bold]   [dim]# or download the app: https://ollama.com/download[/dim]"
        if is_mac else
        "  [bold]curl -fsSL https://ollama.com/install.sh | sh[/bold]"
    )
    alt_line = (
        "  [dim]Linux:[/dim] [bold]curl -fsSL https://ollama.com/install.sh | sh[/bold]"
        if is_mac else
        "  [dim]macOS:[/dim] [bold]brew install ollama[/bold]"
    )
    console.print(Panel.fit(
        "ronin can run on a [bold]100% local[/bold] open-source model with [bold]no API key[/bold] "
        "— it just needs [bold]Ollama[/bold].\n\n"
        "[bold]1.[/bold] Install Ollama:\n"
        f"{install_line}\n"
        f"{alt_line}\n\n"
        "[bold]2.[/bold] Start the server:\n"
        "  [bold]ollama serve[/bold]\n\n"
        "[bold]3.[/bold] Re-run:\n"
        "  [bold]ronin local[/bold]\n\n"
        "[dim]ronin will then pick + download a coding model sized to your RAM and wire it up.[/dim]",
        title="Get ronin running locally", border_style="cyan",
    ))


# --------------------------------------------------------------------------- #
# Orchestrator — the `ronin local` command body.
# --------------------------------------------------------------------------- #
def run(console: "Console", *, model: str | None = None) -> None:
    """Make ronin run fully local on Ollama, with zero API key. Never crashes:

    1. Detect Ollama (binary + server). If missing, print the exact install/serve
       steps and stop gracefully.
    2. Pick a coding model by detected RAM (or use ``--model``) and ``ollama pull`` it
       (skipping the download if it's already installed).
    3. Persist ``provider='ollama'`` + model + base_url via ronin's ``save_config``.
    4. Verify with a tiny real completion through ronin's provider layer.
    5. Print the success banner.
    """
    # Step 1 — detect Ollama (binary, then server).
    binary_present = ollama_installed(shutil.which("ollama") or "")
    if not binary_present:
        _print_install_instructions(console)
        return
    if not ollama_server_reachable():
        _print_install_instructions(console, server_down_only=True)
        return

    # Step 2 — pick the model and ensure it's pulled.
    if model:
        chosen = model
        console.print(f"[dim]using requested model:[/dim] [bold]{chosen}[/bold]")
    else:
        ram_gb = detect_ram_gb()
        chosen = recommend_model(ram_gb)
        ram_note = f"{ram_gb} GB RAM" if ram_gb > 0 else "RAM unknown"
        console.print(f"[dim]detected {ram_note} → picking[/dim] [bold]{chosen}[/bold]")

    already = list_installed_models()
    if chosen in already:
        console.print(f"[green]✓[/green] [bold]{chosen}[/bold] already installed — skipping download.")
    else:
        console.print(f"[cyan]⬇  pulling [bold]{chosen}[/bold] (one-time download)…[/cyan]")
        if not pull_model(chosen, console=console):
            console.print(
                f"[red]✗ couldn't pull [bold]{chosen}[/bold].[/red] "
                f"[dim]Check disk space / network, then retry: [bold]ollama pull {chosen}[/bold]. "
                f"Or pick a smaller model: [bold]ronin local --model qwen2.5-coder:1.5b[/bold].[/dim]"
            )
            return

    # Step 3 — persist the local config via ronin's existing save path.
    try:
        path = apply_local_config(chosen)
        console.print(f"[green]✓[/green] wrote local config → [cyan]{path}[/cyan]")
    except Exception as e:  # noqa: BLE001 — never crash on a config write hiccup
        console.print(f"[red]✗ couldn't save config:[/red] [dim]{e}[/dim]")
        return

    # Step 4 — verify with a tiny real completion through ronin's provider layer.
    console.print("[dim]verifying the model answers locally…[/dim]")
    ok, error = verify_local_completion(chosen)
    if not ok:
        console.print(
            f"[yellow]⚠ config saved, but the verification ping failed:[/yellow] "
            f"[dim]{error}[/dim]\n"
            f"[dim]The model may still be loading. Re-check with [bold]ronin doctor --check[/bold] "
            f"or try [bold]ronin code \"say hi\"[/bold].[/dim]"
        )
        return

    # Step 5 — success banner.
    from rich.panel import Panel

    console.print(Panel.fit(
        "ronin now runs 100% local — no API key 🐼\n\n"
        f"[dim]provider[/dim] ollama  [dim]·[/dim]  [dim]model[/dim] {chosen}  "
        f"[dim]·[/dim]  [dim]endpoint[/dim] {OLLAMA_BASE_URL}\n\n"
        "Try it:\n"
        "  [bold]ronin code[/bold] [cyan]\"add a hello-world endpoint\"[/cyan]\n"
        "  [bold]ronin doctor --check[/bold]   [dim]# confirm end-to-end[/dim]",
        title="Local & private", border_style="green",
    ))
