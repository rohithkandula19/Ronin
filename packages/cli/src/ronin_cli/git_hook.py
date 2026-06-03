"""Install a git pre-commit hook that blocks commits containing secrets.

`ronin hook install` drops (or appends to) ``.git/hooks/pre-commit`` a snippet
that runs ``ronin scan --staged`` and fails the commit if a credential is found.
Idempotent (won't double-add), and append-safe (preserves any existing hook).
Pure string/file logic, unit-tested against a temp repo.
"""
from __future__ import annotations

from pathlib import Path

MARKER = "# >>> ronin secret-scan hook >>>"
_END = "# <<< ronin secret-scan hook <<<"

_SNIPPET = f"""{MARKER}
# Block commits that contain secrets (managed by `ronin hook`).
if command -v ronin >/dev/null 2>&1; then
  ronin scan --staged --quiet || {{
    echo "✗ ronin: commit blocked — staged changes contain a secret. (ronin scan --staged)"; exit 1;
  }}
fi
{_END}
"""


def hook_path(root: Path | str) -> Path:
    return Path(root) / ".git" / "hooks" / "pre-commit"


def is_installed(root: Path | str) -> bool:
    path = hook_path(root)
    return path.is_file() and MARKER in path.read_text(encoding="utf-8")


def install_hook(root: Path | str) -> tuple[Path, bool]:
    """Install the hook. Returns ``(path, newly_installed)``. If a pre-commit hook
    already exists, the snippet is appended; if ours is already present, no-op."""
    path = hook_path(root)
    if not path.parent.is_dir():
        raise FileNotFoundError(f"{path.parent} doesn't exist — is this a git repo?")
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if MARKER in existing:
        return path, False
    if existing.strip():
        body = existing.rstrip() + "\n\n" + _SNIPPET
    else:
        body = "#!/usr/bin/env bash\n" + _SNIPPET
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path, True


def uninstall_hook(root: Path | str) -> bool:
    """Remove just ronin's snippet (preserving any other hook content). Returns
    True if something was removed."""
    path = hook_path(root)
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if MARKER not in text:
        return False
    start = text.index(MARKER)
    end = text.index(_END) + len(_END) if _END in text else len(text)
    cleaned = (text[:start] + text[end:]).strip()
    if cleaned in ("", "#!/usr/bin/env bash"):
        path.unlink()
    else:
        path.write_text(cleaned + "\n", encoding="utf-8")
    return True
