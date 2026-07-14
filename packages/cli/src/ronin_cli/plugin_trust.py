"""Trust gate for plugin files — closes a repo-committable RCE.

THE HOLE THIS CLOSES
--------------------
Plugins live at ``<repo>/.ronin/plugins/*.py`` — i.e. **inside the repository**,
so anyone who can commit to a repo can put a file there. ``build_plugin_tools()``
is called unconditionally on every ``ronin code`` run and imports each of those
files with ``exec_module()``: arbitrary Python, in-process, with the full
privileges of the ronin process.

Crucially that import happens at **load** time — before any tool is called. The
approval gate and the destructive floor guard tool *calls*, so they never get a
say. ``clone a repo -> ronin code`` was therefore remote code execution, with no
prompt and no gate.

The fix has to sit earlier than the gate: at *which plugin files may be imported
at all*.

WHY TRUST IS KEYED ON CONTENT, NOT PATH
---------------------------------------
A plugin the user installed themselves (``ronin plugin new`` / ``add`` /
``from-api``) and one a hostile repo committed land in the *same directory*, so
location proves nothing. Trust is therefore recorded as a sha256 of the file's
bytes, in a **user-global** store (never in the repo — a repo must not be able to
vouch for itself). Editing a trusted plugin changes its digest and silently
revokes trust until re-approved. This is the same model as VS Code workspace
trust or direnv's ``allow``.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

_STORE_NAME = "trusted_plugins.json"


def _store_path() -> Path:
    """User-global trust store. Honors ``RONIN_HOME`` like the rest of ronin.

    Deliberately NOT inside the repo: a repository must never be able to mark its
    own plugins as trusted.
    """
    home = os.environ.get("RONIN_HOME")
    base = Path(home) if home else Path.home() / ".ronin"
    return base / _STORE_NAME


def digest(path: Path | str) -> str:
    """sha256 of the file's bytes — the identity that trust is bound to."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_trusted() -> dict[str, str]:
    """Mapping of absolute plugin path -> trusted content digest."""
    p = _store_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        trusted = data.get("trusted", {})
        return trusted if isinstance(trusted, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(trusted: dict[str, str]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps({"trusted": trusted}, indent=2), encoding="utf-8")
        os.chmod(p, 0o600)
    except OSError:
        pass


def is_trusted(path: Path | str) -> bool:
    """True only if this exact path was trusted AND its content is unchanged.

    Fail-closed: a missing store, an unreadable file, or any digest mismatch is
    NOT trusted. An edited plugin loses trust until the user re-approves it.
    """
    try:
        p = Path(path).resolve()
        recorded = load_trusted().get(str(p))
        if not recorded:
            return False
        return digest(p) == recorded
    except Exception:  # noqa: BLE001 — ANY failure here must read as "not trusted"
        return False


def trust(path: Path | str) -> str:
    """Record this file's current content as trusted. Returns the digest."""
    p = Path(path).resolve()
    d = digest(p)
    trusted = load_trusted()
    trusted[str(p)] = d
    _save(trusted)
    return d


def untrust(path: Path | str) -> bool:
    """Revoke trust. True if it had been trusted."""
    p = str(Path(path).resolve())
    trusted = load_trusted()
    if p in trusted:
        del trusted[p]
        _save(trusted)
        return True
    return False


def untrusted_in(plugin_dir: Path | str) -> list[Path]:
    """Every loadable plugin file in ``plugin_dir`` that is NOT trusted — i.e. the
    files ronin is refusing to import."""
    d = Path(plugin_dir)
    if not d.is_dir():
        return []
    return [
        f for f in sorted(d.glob("*.py"))
        if not f.name.startswith("_") and not is_trusted(f)
    ]
