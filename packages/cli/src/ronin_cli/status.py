"""Premium status line, mode chips, git awareness, and the cost/free badge.

Pure and composable so it's trivially testable: the builders take plain inputs
and return strings / a small dataclass; the caller renders them. The only side
effect is one cheap ``git`` call in :func:`git_state`, which fails closed to
"not a repo" so nothing here ever crashes outside a git checkout.

The headline is :func:`status_text` — the compact, plain-text line shown live in
the input box, e.g.::

    🐼 ronin · FREE · gemini/gemini-flash · normal · main* · 12.4k ctx

and :func:`status_markup`, the Rich-markup variant (adds elapsed) used for the
per-turn footer.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .theme import ACCENT, ERR, MUTE, OK, SOFT, TOOL, WARN

# soft purple for the "review" chip (not in the base palette)
_REVIEW = "#bb9af7"


def _fmt_tokens(n: int) -> str:
    """1234 -> '1.2k', 950 -> '950'. Matches the footer's token formatting."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


# --- cost / free badge -------------------------------------------------------

def cost_badge(config) -> tuple[str, str]:
    """``(label, hex)`` for the active provider+model: FREE / PAID / LOCAL / UNKNOWN.

    - LOCAL — ollama / local / offline: keyless, runs on your machine.
    - FREE  — pricing is known and $0 (free tiers, ``:free`` models).
    - PAID  — pricing is known and non-zero.
    - UNKNOWN — pricing can't be determined (a ``custom`` endpoint or a provider
      not in the price table, with no recognizable model name). Never guessed.
    """
    from .cost import _MODEL_PRICE, _PROVIDER_PRICE, price_for

    provider = config.provider
    if provider in ("ollama", "local") or getattr(config, "offline", False):
        return "LOCAL", TOOL
    model = config.resolved_model() or ""
    low = model.lower()
    model_known = any(frag in low for frag, _ in _MODEL_PRICE)
    # 'custom' lives in the table only as a conservative guess — treat it as
    # genuinely unknown unless the model name itself is recognizable.
    provider_known = provider in _PROVIDER_PRICE and provider != "custom"
    if not (model_known or provider_known):
        return "UNKNOWN", MUTE
    return ("FREE", OK) if price_for(provider, model) == (0.0, 0.0) else ("PAID", WARN)


# --- git awareness -----------------------------------------------------------

@dataclass(frozen=True)
class GitState:
    """A cheap snapshot of the working tree. ``repo`` is False outside git."""
    repo: bool = False
    branch: str = ""
    dirty: bool = False
    changes: int = 0
    ahead: int = 0
    behind: int = 0

    def label(self) -> str:
        """A compact branch label: ``main`` / ``main*`` (dirty) / ``main* ↑1↓2``.
        Empty string when not in a repo."""
        if not self.repo:
            return ""
        s = self.branch or "(detached)"
        if self.dirty:
            s += "*"
        if self.ahead or self.behind:
            s += " "
            if self.ahead:
                s += f"↑{self.ahead}"
            if self.behind:
                s += f"↓{self.behind}"
        return s


def parse_porcelain_branch(stdout: str) -> GitState:
    """Parse ``git status --porcelain -b`` output into a :class:`GitState` (pure)."""
    lines = stdout.splitlines()
    branch, ahead, behind = "", 0, 0
    if lines and lines[0].startswith("##"):
        head = lines[0][2:].strip()
        if head.startswith("No commits yet on "):
            branch = head[len("No commits yet on "):].strip()
        elif "(no branch)" in head or head.startswith("HEAD"):
            branch = "(detached)"
        else:
            branch = head.split("...")[0].strip()
            m = re.search(r"\[(.*)\]", head)
            if m:
                for part in (p.strip() for p in m.group(1).split(",")):
                    if part.startswith("ahead "):
                        ahead = int(part[6:])
                    elif part.startswith("behind "):
                        behind = int(part[7:])
    changes = sum(1 for ln in lines if ln and not ln.startswith("##"))
    return GitState(repo=True, branch=branch, dirty=changes > 0, changes=changes,
                    ahead=ahead, behind=behind)


def git_state(root, *, timeout: int = 5) -> GitState:
    """Branch + dirty + ahead/behind for ``root``. Never raises; returns a
    not-a-repo :class:`GitState` if git is missing, errors, or ``root`` isn't a
    checkout."""
    try:
        r = subprocess.run(
            ["git", "-C", str(Path(root).resolve()), "status", "--porcelain", "-b"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return GitState()
    if r.returncode != 0:
        return GitState()
    return parse_porcelain_branch(r.stdout)


# --- mode chips --------------------------------------------------------------

# name -> (label, hex). The chips tell the user what ronin is allowed to do.
_CHIP_STYLE: dict[str, tuple[str, str]] = {
    "normal": ("normal", MUTE),
    "plan": ("plan", TOOL),
    "auto-accept": ("auto-accept", WARN),
    "offline": ("offline", "#7aa2f7"),  # slate-blue, distinct from plan's sky
    "free": ("free", OK),
    "scout": ("scout", ACCENT),
    "review": ("review", _REVIEW),
}


def mode_chip(name: str) -> tuple[str, str]:
    """``('◈ <label>', hex)`` for a mode name (unknown names render plainly)."""
    label, hex_ = _CHIP_STYLE.get(name, (name, MUTE))
    return f"◈ {label}", hex_


def active_modes(config, edit_mode: str = "normal") -> list[str]:
    """The mode names currently in effect, edit-mode first.

    Reflects what ronin may do this session: the edit mode (normal / plan /
    auto-accept), plus free / offline / scout / review when those are on.
    """
    modes = [edit_mode if edit_mode in _CHIP_STYLE else "normal"]
    if cost_badge(config)[0] == "FREE":
        modes.append("free")
    if getattr(config, "offline", False):
        modes.append("offline")
    if getattr(config, "route_fast", None) and getattr(config, "route_strong", None):
        modes.append("scout")
    if getattr(config, "verify", False):
        modes.append("review")
    return modes


def render_chips(config, edit_mode: str = "normal") -> str:
    """Rich-markup row of the active mode chips."""
    out = []
    for name in active_modes(config, edit_mode):
        label, hex_ = mode_chip(name)
        out.append(f"[{hex_}]{label}[/{hex_}]")
    return " ".join(out)


# --- the status line ---------------------------------------------------------

def status_segments(
    config,
    root,
    *,
    edit_mode: str = "normal",
    ctx_tokens: int | None = None,
    elapsed: float | None = None,
    git: GitState | None = None,
    brand: bool = True,
) -> list[tuple[str, str]]:
    """Ordered ``(text, hex)`` segments of the status line — the single source of
    truth both renderers build from. Pass a precomputed ``git`` to skip the
    subprocess (tests / hot paths)."""
    badge, badge_hex = cost_badge(config)
    g = git if git is not None else git_state(root)
    segs: list[tuple[str, str]] = []
    if brand:
        segs.append(("🐼 ronin", ACCENT))
    segs.append((badge, badge_hex))
    segs.append((f"{config.provider}/{config.resolved_model()}", SOFT))
    segs.append((edit_mode, _CHIP_STYLE.get(edit_mode, (edit_mode, MUTE))[1]))
    if g.label():
        segs.append((g.label(), WARN if g.dirty else OK))
    if ctx_tokens:
        segs.append((f"{_fmt_tokens(ctx_tokens)} ctx", MUTE))
    if elapsed is not None:
        segs.append((f"{elapsed:.0f}s", MUTE))
    return segs


def status_text(config, root, *, edit_mode: str = "normal",
                ctx_tokens: int | None = None, git: GitState | None = None,
                brand: bool = False) -> str:
    """Compact plain-text status for the live input-box toolbar (no markup).

    e.g. ``FREE · gemini/gemini-flash · normal · main* · 12.4k ctx``
    """
    segs = status_segments(config, root, edit_mode=edit_mode, ctx_tokens=ctx_tokens,
                           git=git, brand=brand)
    return " · ".join(text for text, _ in segs)


def status_markup(config, root, *, edit_mode: str = "normal",
                  ctx_tokens: int | None = None, elapsed: float | None = None,
                  git: GitState | None = None, brand: bool = True) -> str:
    """Rich-markup status line for the per-turn footer (adds elapsed)."""
    segs = status_segments(config, root, edit_mode=edit_mode, ctx_tokens=ctx_tokens,
                           elapsed=elapsed, git=git, brand=brand)
    return f"  [{MUTE}]·[/{MUTE}]  ".join(f"[{hex_}]{text}[/{hex_}]" for text, hex_ in segs)


# --- the chip strip ----------------------------------------------------------

def gate_label(edit_mode: str, *, full_access: bool = False,
               read_only: bool = False) -> str | None:
    """The write-gate chip text, or None when the mode chip already conveys it.

    - read-only mode/role -> "read-only"
    - auto-accept / full-access -> None (the mode chip already says auto-accept)
    - otherwise -> "write-gated" (the safety default: edits need approval)
    """
    if read_only or edit_mode == "plan":
        return "read-only"
    if full_access or edit_mode == "auto-accept":
        return None
    return "write-gated"


def chip_strip(config, root, *, edit_mode: str = "normal", role: str | None = None,
               width: int = 80, git: GitState | None = None,
               full_access: bool | None = None) -> str:
    """A compact, bracketed, width-aware chip row, e.g.::

        [FREE] [cerebras:gpt-oss-120b] [normal] [main*] [write-gated]

    Always shows the cost badge and mode; appends the write-gate, git branch, and
    role chips as space allows. Under a narrow terminal it sheds the
    lowest-priority chips first (role, then branch, then model) but never the
    badge / mode / gate (the cost + safety signals). Never crashes outside git.
    """
    from .roles import role_is_read_only

    if full_access is None:
        full_access = getattr(config, "full_access", False)
    badge, _ = cost_badge(config)
    g = git if git is not None else git_state(root)
    read_only = bool(role and role_is_read_only(role))

    # (text, drop_priority) — higher priority is kept longer under narrow width.
    items: list[tuple[str, int]] = [
        (badge, 100),                                              # cost — always
        (f"{config.provider}:{config.resolved_model()}", 50),     # what's running
        (edit_mode, 90),                                          # mode — safety
    ]
    gate = gate_label(edit_mode, full_access=full_access, read_only=read_only)
    if gate:
        items.append((gate, 85))                                  # gate — safety
    if g.label():
        items.append((g.label(), 30))                            # git
    if role:
        items.append((f"role:{role}", 20))                       # role

    def _render(keep: set[int]) -> str:
        return " ".join(f"[{items[i][0]}]" for i in range(len(items)) if i in keep)

    keep = set(range(len(items)))
    line = _render(keep)
    if len(line) <= width:
        return line
    # shed lowest-priority chips (preserving order) until it fits
    for i in sorted(keep, key=lambda j: items[j][1]):
        if len(_render(keep)) <= width:
            break
        keep.discard(i)
    return _render(keep)
