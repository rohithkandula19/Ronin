"""Workspace checkpoint & rewind — snapshot the whole working tree before a
risky change, roll back with one command.

``/undo`` reverts the last single file edit; that's not enough before a big
multi-file refactor or a fleet of parallel agents. A checkpoint captures the
*entire* working tree — tracked AND untracked files — as a git commit object on
a private ref, without touching your real index, branch, or history. Rewinding
restores every file to that snapshot and removes files created since, so you get
back exactly the state you checkpointed.

It's git plumbing under the hood (write-tree / commit-tree / checkout-index) but
leaves your normal git state completely untouched — the snapshots live under
``refs/ronin/checkpoints`` and a small ``.ronin/checkpoints.json`` index.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .worktree import NotAGitRepo, is_git_repo

_META = Path(".ronin") / "checkpoints.json"
_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "ronin", "GIT_AUTHOR_EMAIL": "ronin@local",
    "GIT_COMMITTER_NAME": "ronin", "GIT_COMMITTER_EMAIL": "ronin@local",
}


def _git(root: Path, *args: str, env: dict | None = None, check: bool = True):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, env=full_env, check=check)


@dataclass
class Checkpoint:
    id: int
    sha: str
    label: str
    created: float


def _meta_path(root: Path) -> Path:
    return root / _META


def _load_meta(root: Path) -> list[dict]:
    p = _meta_path(root)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_meta(root: Path, rows: list[dict]) -> None:
    p = _meta_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def create_checkpoint(root: Path | str, label: str = "", *, now: float | None = None) -> Checkpoint:
    """Snapshot the full working tree (tracked + untracked) into a commit object
    on a ronin ref, without disturbing the index/branch/HEAD."""
    root = Path(root).resolve()
    if not is_git_repo(root):
        raise NotAGitRepo(f"{root} is not a git repository — checkpoints need git")

    # Build a throwaway index that mirrors the current working tree exactly.
    fd, idx = tempfile.mkstemp(prefix="ronin-cp-index-")
    os.close(fd)
    try:
        env = {"GIT_INDEX_FILE": idx, **_AUTHOR_ENV}
        _git(root, "read-tree", "HEAD", env=env, check=False)  # seed (no-op on empty repo)
        _git(root, "add", "-A", env=env)
        tree = _git(root, "write-tree", env=env).stdout.strip()
        parent = _git(root, "rev-parse", "HEAD", check=False).stdout.strip()
        commit_args = ["commit-tree", tree, "-m", f"ronin-checkpoint: {label or 'snapshot'}"]
        if parent and len(parent) == 40:
            commit_args += ["-p", parent]
        sha = _git(root, *commit_args, env=env).stdout.strip()
    finally:
        Path(idx).unlink(missing_ok=True)

    rows = _load_meta(root)
    cid = (max((r["id"] for r in rows), default=0)) + 1
    _git(root, "update-ref", f"refs/ronin/checkpoints/{cid}", sha, check=False)
    cp = Checkpoint(id=cid, sha=sha, label=label or "snapshot",
                    created=now if now is not None else time.time())
    rows.append(asdict(cp))
    _save_meta(root, rows)
    return cp


def list_checkpoints(root: Path | str) -> list[Checkpoint]:
    return [Checkpoint(**r) for r in _load_meta(Path(root).resolve())]


def checkpoint_details(root: Path | str) -> list[dict]:
    """Richer, read-only checkpoint listing for the resume UI — id, created_at
    (ISO), sha, label, and the tracked-file count captured in that snapshot."""
    import datetime as _dt

    root = Path(root).resolve()
    out: list[dict] = []
    for cp in list_checkpoints(root):
        try:
            created_at = _dt.datetime.fromtimestamp(cp.created).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            created_at = ""
        try:
            files = len(_snapshot_files(root, cp.sha))
        except (OSError, subprocess.SubprocessError):
            files = 0
        out.append({"id": cp.id, "created_at": created_at, "sha": cp.sha,
                    "short_sha": cp.sha[:8], "label": cp.label, "tracked_files": files})
    return out


def _snapshot_files(root: Path, sha: str) -> set[str]:
    out = _git(root, "ls-tree", "-r", "--name-only", sha, check=False)
    return {line for line in out.stdout.splitlines() if line}


def _current_files(root: Path) -> set[str]:
    tracked = _git(root, "ls-files", check=False).stdout.splitlines()
    untracked = _git(root, "ls-files", "--others", "--exclude-standard",
                     check=False).stdout.splitlines()
    return {f for f in tracked + untracked if f}


def restore_checkpoint(root: Path | str, cid: int) -> str:
    """Restore every file to checkpoint ``cid`` and delete files created since —
    a true rewind. Ignored files (.venv, node_modules, …) are never touched."""
    root = Path(root).resolve()
    if not is_git_repo(root):
        raise NotAGitRepo(f"{root} is not a git repository")
    rows = _load_meta(root)
    match = next((r for r in rows if r["id"] == cid), None)
    if match is None:
        return f"no checkpoint #{cid} (use list_checkpoints to see them)"
    sha = match["sha"]

    snap = _snapshot_files(root, sha)
    current = _current_files(root)

    # Restore the snapshot's files into the working tree via a throwaway index.
    fd, idx = tempfile.mkstemp(prefix="ronin-restore-index-")
    os.close(fd)
    try:
        env = {"GIT_INDEX_FILE": idx, **_AUTHOR_ENV}
        _git(root, "read-tree", sha, env=env)
        _git(root, "checkout-index", "-f", "-a", env=env)
    finally:
        Path(idx).unlink(missing_ok=True)

    # Remove files that were created after the checkpoint (true rewind).
    removed = 0
    for rel in current - snap:
        target = root / rel
        try:
            if target.is_file():
                target.unlink()
                removed += 1
        except OSError:
            pass

    return (f"rewound to checkpoint #{cid} ({match['label']}): "
            f"restored {len(snap)} file(s), removed {removed} created since.")


def build_checkpoint_tools(root: Path | str = "."):
    """Three tools: checkpoint (snapshot), list_checkpoints (read-only), and
    rewind (restore — SENSITIVE, gated like any destructive op)."""
    from ronin_agent_patterns import Tool

    def checkpoint(label: str = "") -> str:
        try:
            cp = create_checkpoint(root, label)
        except NotAGitRepo as e:
            return f"cannot checkpoint: {e}"
        return (f"created checkpoint #{cp.id} ({cp.label}). "
                f"rewind(id={cp.id}) restores the whole workspace to this point.")

    def list_checkpoints_tool() -> str:
        cps = list_checkpoints(root)
        if not cps:
            return "no checkpoints yet — call checkpoint to make one."
        return "\n".join(f"#{c.id} {c.label}" for c in cps)

    def rewind(id: int) -> str:
        try:
            return restore_checkpoint(root, int(id))
        except NotAGitRepo as e:
            return f"cannot rewind: {e}"

    def _tool(name, desc, schema, handler) -> Tool:
        return Tool(name=name, description=desc, input_schema=schema, handler=handler)

    return [
        _tool("checkpoint",
              "Snapshot the ENTIRE workspace (all files, tracked + untracked) before a risky "
              "multi-file change, so you can roll back as a unit if it goes wrong. Cheap and "
              "non-destructive. Args: label (short description).",
              {"type": "object", "properties": {"label": {"type": "string"}}},
              checkpoint),
        _tool("list_checkpoints", "List saved workspace checkpoints (id + label).",
              {"type": "object", "properties": {}},
              list_checkpoints_tool),
        _tool("rewind",
              "Restore the WHOLE workspace to a checkpoint: every file goes back to its "
              "snapshot and files created since are removed. SENSITIVE — gated by approval. "
              "Args: id (checkpoint id).",
              {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
              rewind),
    ]
