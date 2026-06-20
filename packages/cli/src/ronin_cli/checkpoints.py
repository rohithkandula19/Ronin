"""Session checkpoints + ``ronin undo`` — snapshot the repo *before* a coding
session so a messy agent run can be rolled back in one move.

This is the user-facing safety net (the command surface ``ronin checkpoint`` /
``ronin undo`` wires to). It is deliberately separate from the agent-facing
``checkpoint.py`` tool layer: this module carries human-readable string ids, a
non-git fallback, an ``auto_checkpoint`` hook for the start of a session, and a
*reversible* restore. The two can coexist — they write to different namespaces
(``refs/ronin/session/*`` here vs ``refs/ronin/checkpoints/*`` there, and
``session.json`` vs ``checkpoints.json``).

How a snapshot is stored
------------------------
* **Inside a git repo** — the working tree (tracked + untracked, minus ignored)
  is committed into a *throwaway* index and parked on a private ref
  ``refs/ronin/session/<id>``. Your real index, branch, HEAD and history are
  never touched: ``git stash`` is not used, nothing is checked out, the working
  tree is left exactly as it was. (Closest analogy: ``git stash create`` — we
  build a commit object but never apply or pop it.)
* **Outside git** — every tracked-ish file under the root is *copied* into
  ``~/.ronin/checkpoints/<root-hash>/<id>/`` so plain directories still get a
  safety net. Vendored/junk dirs (``.git``, ``.venv``, ``node_modules``, …) are
  skipped so a snapshot stays cheap.

Safety contract for restore (READ THIS)
----------------------------------------
``restore_checkpoint`` is built to never lose work silently:

1. **Reversible.** Before restoring anything it takes a fresh "safety"
   checkpoint of the *current* state (label ``auto:pre-restore``). If a restore
   was a mistake, that snapshot id is returned so you can restore *it*.
2. **No silent deletes.** Files created since the snapshot are reported in the
   returned record's ``would_remove`` list and removed only as part of the
   rewind — never anything ignored (``.venv`` etc.), and the impure caller is
   expected to have confirmed first. The pure ``restore_plan`` lets a UI show
   exactly what will change *before* committing to it.
3. **Confirmation surface.** Both ``create``/``restore`` return plain dicts /
   records (not just a bool) so the command layer can print a clear summary and
   gate destructive restores behind a y/N.

Only stdlib + subprocess(git) here (rich lives in the command layer).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .worktree import NotAGitRepo, is_git_repo

# Where the file-copy fallback stores snapshots for non-git roots. Mirrors the
# config module's user dir convention (~/.ronin/...).
USER_CHECKPOINT_DIR = Path.home() / ".ronin" / "checkpoints"

# Private ref namespace + on-disk index for git-backed snapshots. Distinct from
# checkpoint.py's refs/ronin/checkpoints so the two layers never collide.
_REF_PREFIX = "refs/ronin/session"
_META = Path(".ronin") / "session.json"

# Directories never worth snapshotting — vendored deps + VCS metadata. Kept in
# sync (by intent) with the repo-map ignore policy; duplicated here so this
# module has zero import surface beyond worktree/config.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build",
    ".ronin", ".csk", ".idea", ".vscode", ".DS_Store",
}
_SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}

# Identity for the snapshot commit objects so they never inherit (or require) a
# configured user.name/email — a snapshot must work in a freshly-init'd repo.
_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "ronin", "GIT_AUTHOR_EMAIL": "ronin@local",
    "GIT_COMMITTER_NAME": "ronin", "GIT_COMMITTER_EMAIL": "ronin@local",
}

# Base-36 epoch keeps ids short *and* lexically sortable (newer id > older id as
# a string), which is what lets most_recent break ties without parsing time back
# out. The prefix is zero-padded to a FIXED width so unequal-length encodings
# still compare in numeric order ("000000rs" < "00016v7k"); 9 digits covers
# epochs to 36**9 ≈ year 3.2M, so it never overflows in practice.
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
_TIME_WIDTH = 9


# ───────────────────────────── PURE helpers ──────────────────────────────────
# No I/O, no git, no clock of their own — every time-dependent function takes an
# explicit ``now``. These are the unit-tested core.

def _b36(n: int) -> str:
    """Encode a non-negative int in base-36 (lowercase). Pure."""
    n = int(n)
    if n <= 0:
        return "0"
    out: list[str] = []
    while n:
        n, r = divmod(n, 36)
        out.append(_B36[r])
    return "".join(reversed(out))


def slugify(label: str | None, *, max_len: int = 24) -> str:
    """Turn a free-text label into a short, filesystem + ref-safe slug.

    Lowercases, collapses any run of non-alphanumerics to a single ``-``, trims
    leading/trailing dashes, and caps the length. Empty/None → ``"snapshot"``.
    Pure — used by both the id and the display layer.
    """
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
    s = s[:max_len].strip("-")
    return s or "snapshot"


def new_checkpoint_id(label: str | None, *, now: float) -> str:
    """Build a sortable, human-legible checkpoint id from ``label`` + ``now``.

    Format: ``<base36-epoch-9wide>-<slug>`` e.g. ``"00000rs9k-before-refactor"``.
    The zero-padded base-36 time prefix sorts chronologically as a plain string,
    so newer ids compare greater than older ones (see ``most_recent``). Pure:
    identical ``(label, now)`` always yields the identical id.
    """
    return f"{_b36(int(now)).rjust(_TIME_WIDTH, '0')}-{slugify(label)}"


def _id_time_prefix(cid: str) -> str:
    """The sortable time prefix of an id (everything before the first '-')."""
    return cid.split("-", 1)[0]


def parse_checkpoint(meta: dict) -> dict:
    """Normalise a raw stored record into a clean, fully-populated dict.

    Tolerant of partial/legacy rows: missing fields get sane defaults and a
    ``files`` count is always an int. Never raises — a corrupt row degrades to a
    best-effort entry rather than bringing down ``list``/``summarize``. Pure.

    Returns keys: ``id, label, created, files, kind, ref, sha, path, auto``.
    """
    cid = str(meta.get("id") or "")
    try:
        created = float(meta.get("created") or 0.0)
    except (TypeError, ValueError):
        created = 0.0
    try:
        files = int(meta.get("files") or 0)
    except (TypeError, ValueError):
        files = 0
    return {
        "id": cid,
        "label": str(meta.get("label") or "snapshot"),
        "created": created,
        "files": files,
        "kind": str(meta.get("kind") or "git"),  # "git" | "copy"
        "ref": meta.get("ref"),
        "sha": meta.get("sha"),
        "path": meta.get("path"),
        "auto": bool(meta.get("auto", False)),
    }


def most_recent(records: list[dict]) -> dict | None:
    """Return the newest checkpoint record, or None if there are none.

    Newest = greatest ``created`` timestamp, with the lexically-greatest id as a
    deterministic tiebreak (ids embed a base-36 time prefix, so this also breaks
    ties created within the same second insertion-stably). Pure.
    """
    parsed = [parse_checkpoint(r) for r in records or []]
    if not parsed:
        return None
    return max(parsed, key=lambda r: (r["created"], _id_time_prefix(r["id"]), r["id"]))


def _fmt_age(created: float, *, now: float) -> str:
    """Render an age like ``"5m ago"`` / ``"2h ago"`` / ``"just now"``. Pure."""
    if not created:
        return "—"
    secs = max(0, int(now - created))
    if secs < 5:
        return "just now"
    for unit, label in ((86400, "d"), (3600, "h"), (60, "m"), (1, "s")):
        if secs >= unit:
            return f"{secs // unit}{label} ago"
    return "just now"


def summarize_checkpoints(records: list[dict], *, now: float | None = None) -> str:
    """A plain-text table of checkpoints (id · label · when · files).

    Newest first. ``now`` (defaults to ``time.time()`` only for the relative
    "when" column) keeps the body deterministic when supplied — pass it in tests.
    Returns a friendly one-liner when there are none. Pure aside from the
    optional default clock.
    """
    now = time.time() if now is None else now
    parsed = sorted(
        (parse_checkpoint(r) for r in records or []),
        key=lambda r: (r["created"], r["id"]),
        reverse=True,
    )
    if not parsed:
        return "no checkpoints yet — run `ronin checkpoint` to make one."

    rows = [(p["id"], p["label"] + (" (auto)" if p["auto"] else ""),
             _fmt_age(p["created"], now=now), str(p["files"])) for p in parsed]
    headers = ("id", "label", "when", "files")
    widths = [len(h) for h in headers]
    for r in rows:
        widths = [max(w, len(c)) for w, c in zip(widths, r)]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    out = [line(headers), line(tuple("-" * w for w in widths))]
    out += [line(r) for r in rows]
    return "\n".join(out)


def restore_plan(snapshot_files: set[str], current_files: set[str]) -> dict:
    """Pure diff between a snapshot's file set and the current one.

    Returns ``{restore, would_remove}`` — files to write back from the snapshot,
    and files created since the snapshot that a true rewind would delete. Lets a
    UI show the blast radius *before* anything is touched. Sorted for stability.
    """
    return {
        "restore": sorted(snapshot_files),
        "would_remove": sorted(current_files - snapshot_files),
    }


@dataclass
class Checkpoint:
    """A stored snapshot. ``ref``/``sha`` are set for git snapshots; ``path`` for
    the file-copy fallback. ``files`` is the number of files captured."""
    id: str
    label: str
    created: float
    files: int = 0
    kind: str = "git"  # "git" | "copy"
    ref: str | None = None
    sha: str | None = None
    path: str | None = None
    auto: bool = False

    def to_meta(self) -> dict:
        return asdict(self)


@dataclass
class RestoreResult:
    """Outcome of a restore — the confirmation surface the command layer prints.

    ``safety_id`` is the id of the pre-restore snapshot taken of the *old* state,
    so the whole operation is reversible (`ronin undo <safety_id>`)."""
    ok: bool
    id: str
    label: str
    restored: int = 0
    removed: int = 0
    safety_id: str | None = None
    message: str = ""


# ─────────────────────────── impure git/IO layer ─────────────────────────────

def _git(root: Path, *args: str, env: dict | None = None, check: bool = False):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, env=full_env, check=check)


def _meta_path(root: Path) -> Path:
    return root / _META


def _load_meta(root: Path) -> list[dict]:
    p = _meta_path(root)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_meta(root: Path, rows: list[dict]) -> None:
    p = _meta_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _root_hash(root: Path) -> str:
    """Stable short hash of the absolute root path — namespaces the file-copy
    store so two different projects never share a checkpoint directory."""
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]


def _copy_dir(root: Path) -> Path:
    return USER_CHECKPOINT_DIR / _root_hash(root)


def _iter_files(root: Path):
    """Yield POSIX-relative paths of snapshot-worthy files under ``root`` (the
    file-copy fallback's notion of 'tracked'). Skips vendored/junk dirs + files."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name in _SKIP_FILES:
                continue
            rel = (Path(dirpath) / name).relative_to(root)
            yield rel.as_posix()


# ---- git snapshot primitives ----

def _git_snapshot(root: Path, cid: str, label: str) -> tuple[str, str, int]:
    """Commit the current working tree onto ``refs/ronin/session/<cid>`` via a
    throwaway index — the real index/branch/HEAD are untouched. Returns
    ``(ref, sha, file_count)``."""
    fd, idx = tempfile.mkstemp(prefix="ronin-sess-index-")
    os.close(fd)
    try:
        env = {"GIT_INDEX_FILE": idx, **_AUTHOR_ENV}
        _git(root, "read-tree", "HEAD", env=env)            # seed (no-op on empty repo)
        _git(root, "add", "-A", env=env)                    # mirror working tree incl. untracked
        tree = _git(root, "write-tree", env=env).stdout.strip()
        parent = _git(root, "rev-parse", "HEAD").stdout.strip()
        args = ["commit-tree", tree, "-m", f"ronin-session: {label}"]
        if parent and len(parent) == 40:
            args += ["-p", parent]
        sha = _git(root, *args, env=env).stdout.strip()
        count = len([f for f in _git(root, "ls-tree", "-r", "--name-only",
                                     sha).stdout.splitlines() if f])
    finally:
        Path(idx).unlink(missing_ok=True)
    ref = f"{_REF_PREFIX}/{cid}"
    _git(root, "update-ref", ref, sha)
    return ref, sha, count


def _in_skip_dir(rel: str) -> bool:
    """True if a POSIX-relative path lives under a skipped dir (``.ronin``,
    ``.venv``, …). Keeps ronin's own state out of the rewind's delete set so a
    restore never wipes ``.ronin/session.json`` (the checkpoint records) or the
    safety snapshot. Pure."""
    return any(part in _SKIP_DIRS for part in rel.split("/"))


def _git_snapshot_files(root: Path, sha: str) -> set[str]:
    out = _git(root, "ls-tree", "-r", "--name-only", sha)
    return {ln for ln in out.stdout.splitlines() if ln and not _in_skip_dir(ln)}


def _git_current_files(root: Path) -> set[str]:
    tracked = _git(root, "ls-files").stdout.splitlines()
    untracked = _git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return {f for f in tracked + untracked if f and not _in_skip_dir(f)}


# ---- file-copy fallback primitives ----

def _copy_snapshot(root: Path, cid: str) -> tuple[Path, int]:
    """Copy every snapshot-worthy file under ``root`` into the per-id store.
    Returns ``(snapshot_dir, file_count)``."""
    dest = _copy_dir(root) / cid
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    count = 0
    for rel in _iter_files(root):
        src = root / rel
        tgt = dest / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, tgt)
            count += 1
        except OSError:
            pass
    return dest, count


def _copy_snapshot_files(snap_dir: Path) -> set[str]:
    if not snap_dir.is_dir():
        return set()
    return {p.relative_to(snap_dir).as_posix()
            for p in snap_dir.rglob("*") if p.is_file()}


# ────────────────────────── public impure surface ────────────────────────────

def create_checkpoint(root: Path | str, label: str | None = None, *,
                       now: float | None = None, auto: bool = False) -> Checkpoint:
    """Snapshot the repo at ``root`` and record it. Non-destructive: the working
    tree, index and git history are left exactly as they were.

    Uses the git ref mechanism inside a repo, the file-copy fallback outside one.
    Returns the stored :class:`Checkpoint` (its ``id`` is what ``ronin undo``
    takes). ``auto=True`` tags it as a session/auto snapshot for display.
    """
    root = Path(root).resolve()
    ts = time.time() if now is None else now
    cid = new_checkpoint_id(label, now=ts)

    rows = _load_meta(root)
    # Guarantee id uniqueness even for two snapshots in the same second / label.
    if any(r.get("id") == cid for r in rows):
        suffix = 2
        while any(r.get("id") == f"{cid}-{suffix}" for r in rows):
            suffix += 1
        cid = f"{cid}-{suffix}"

    if is_git_repo(root):
        ref, sha, count = _git_snapshot(root, cid, label or "snapshot")
        cp = Checkpoint(id=cid, label=label or "snapshot", created=ts, files=count,
                        kind="git", ref=ref, sha=sha, auto=auto)
    else:
        snap_dir, count = _copy_snapshot(root, cid)
        cp = Checkpoint(id=cid, label=label or "snapshot", created=ts, files=count,
                        kind="copy", path=str(snap_dir), auto=auto)

    rows.append(cp.to_meta())
    _save_meta(root, rows)
    return cp


def list_checkpoints(root: Path | str) -> list[Checkpoint]:
    """All recorded checkpoints for ``root``, newest first."""
    rows = _load_meta(Path(root).resolve())
    parsed = sorted((parse_checkpoint(r) for r in rows),
                    key=lambda r: (r["created"], r["id"]), reverse=True)
    return [Checkpoint(**p) for p in parsed]


def auto_checkpoint(root: Path | str, *, now: float | None = None) -> Checkpoint | None:
    """Take a 'session start' snapshot — call this once before a coding session.

    Best-effort: returns the :class:`Checkpoint` on success, or None if anything
    goes wrong (it must never block a session from starting). Skips the work when
    there is nothing to capture in a git repo with a clean tree.
    """
    try:
        root = Path(root).resolve()
        if is_git_repo(root):
            dirty = _git(root, "status", "--porcelain").stdout.strip()
            head = _git(root, "rev-parse", "HEAD").stdout.strip()
            if not dirty and head:
                # Clean tree on a real commit: HEAD already *is* the restore
                # point, so a session snapshot would be redundant noise.
                return None
        return create_checkpoint(root, "session start", now=now, auto=True)
    except Exception:  # noqa: BLE001 - auto-checkpoint must never break a session
        return None


def restore_checkpoint(root: Path | str, cid: str, *,
                       now: float | None = None) -> RestoreResult:
    """Roll the repo back to checkpoint ``cid`` — SAFELY and REVERSIBLY.

    Before touching anything this first takes a *safety* checkpoint of the
    current state (``auto:pre-restore``); its id is returned in
    ``RestoreResult.safety_id`` so the restore itself can be undone. Files
    created since the snapshot are removed as part of the rewind, but ignored
    files (``.venv`` etc.) are never touched and nothing outside the snapshot's
    own tree is deleted. Returns a :class:`RestoreResult` describing exactly what
    happened — the caller is expected to have confirmed with the user first
    (``ronin undo`` gates on y/N).

    Returns ``ok=False`` (no changes made) if there is no such checkpoint.
    """
    root = Path(root).resolve()
    rows = _load_meta(root)
    match = next((parse_checkpoint(r) for r in rows if str(r.get("id")) == str(cid)), None)
    if match is None:
        return RestoreResult(ok=False, id=str(cid), label="",
                             message=f"no checkpoint '{cid}' — see `ronin checkpoint --list`.")

    # 1) Reversibility: snapshot the CURRENT state before we overwrite it.
    safety_id: str | None = None
    try:
        safety = create_checkpoint(root, "pre-restore", now=now, auto=True)
        safety_id = safety.id
    except Exception:  # noqa: BLE001 - never let the safety net block the restore itself
        safety_id = None

    if match["kind"] == "git":
        sha = match["sha"]
        snap = _git_snapshot_files(root, sha)
        current = _git_current_files(root)
        plan = restore_plan(snap, current)
        # Restore the snapshot's tree into the working dir via a throwaway index.
        fd, idx = tempfile.mkstemp(prefix="ronin-restore-index-")
        os.close(fd)
        try:
            env = {"GIT_INDEX_FILE": idx, **_AUTHOR_ENV}
            _git(root, "read-tree", sha, env=env)
            _git(root, "checkout-index", "-f", "-a", env=env)
        finally:
            Path(idx).unlink(missing_ok=True)
    else:  # file-copy fallback
        snap_dir = Path(match["path"]) if match.get("path") else _copy_dir(root) / cid
        snap = _copy_snapshot_files(snap_dir)
        current = set(_iter_files(root))
        plan = restore_plan(snap, current)
        for rel in plan["restore"]:
            src = snap_dir / rel
            tgt = root / rel
            tgt.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, tgt)
            except OSError:
                pass

    # Remove files created since the snapshot (true rewind). Only ever paths that
    # were captured-or-not by the snapshot's own tree; ignored files aren't here.
    removed = 0
    for rel in plan["would_remove"]:
        tgt = root / rel
        try:
            if tgt.is_file():
                tgt.unlink()
                removed += 1
        except OSError:
            pass

    return RestoreResult(
        ok=True, id=match["id"], label=match["label"],
        restored=len(snap), removed=removed, safety_id=safety_id,
        message=(f"restored {len(snap)} file(s), removed {removed} created since"
                 + (f"; reversible via `ronin undo {safety_id}`" if safety_id else "")),
    )
