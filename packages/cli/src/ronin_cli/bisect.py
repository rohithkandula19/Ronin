"""Autonomous regression hunting — `ronin bisect`.

Give ronin a command that *fails now but passed before* and it drives
``git bisect`` to find the exact commit that introduced the failure — running in
an isolated worktree so your checkout is never disturbed — then surfaces that
commit's diff for an AI root-cause analysis. git-bisect + an LLM oracle in one
step. The output parsing and command building are pure and unit-tested; the
bisect itself runs sandboxed.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_FIRST_BAD_RE = re.compile(r"\b([0-9a-f]{7,40}) is the first bad commit", re.IGNORECASE)


@dataclass
class BisectResult:
    culprit: str | None          # sha of the first bad commit
    subject: str = ""            # its commit subject
    diff: str = ""               # its diff
    note: str = ""


def parse_first_bad(output: str) -> str | None:
    """Extract the '<sha> is the first bad commit' line from git bisect run output."""
    m = _FIRST_BAD_RE.search(output or "")
    return m.group(1) if m else None


#: How much of git's own output travels with a failure. Enough to hold the reason
#: git gave, short enough that the note is still a note.
NOTE_OUTPUT_CHARS = 600


def _git_note(head: str, proc: subprocess.CompletedProcess[str]) -> str:
    """One failure note shape: what we were doing, git's exit code, git's own words.

    Every failure in :func:`run_bisect` used to interpolate a bare slice of stderr,
    which produced ``"bisect start failed: "`` — a message ending in a colon with
    nothing after it — whenever git failed silently. Worse, the no-culprit case threw
    the output away entirely and printed the *most likely* cause as though it were the
    finding, so a clone that checked out nothing, an oracle that could not find its
    script, and a bisect where every commit tested good all arrived looking identical.
    That is an unfalsifiable error message, and it is why a CI failure nobody could
    reproduce locally could not be diagnosed at all.

    Truncation is marked, like every other truncation in this project.
    """
    detail = (proc.stderr or proc.stdout or "").strip()
    if len(detail) > NOTE_OUTPUT_CHARS:
        cut = len(detail) - NOTE_OUTPUT_CHARS
        detail = f"{detail[:NOTE_OUTPUT_CHARS]}…[{cut} more chars]"
    said = f"git said: {detail}" if detail else "git printed nothing."
    return f"{head} (git exited {proc.returncode}) {said}"


def _no_culprit_note(run: subprocess.CompletedProcess[str]) -> str:
    """Why bisect named no first bad commit. The guess is kept, but labelled."""
    return _git_note(
        "couldn't isolate a single bad commit. Most often the command fails on the "
        "'good' commit too — check that it passes there.",
        run,
    )


def default_good(root: Path | str) -> str | None:
    """A sensible 'known-good' starting point: the most recent tag, else ~20
    commits back, else the root commit."""
    def _g(*args):
        return subprocess.run(["git", "-C", str(Path(root).resolve()), *args],
                              capture_output=True, text=True, timeout=15)
    tag = _g("describe", "--tags", "--abbrev=0")
    if tag.returncode == 0 and tag.stdout.strip():
        return tag.stdout.strip()
    back = _g("rev-parse", "HEAD~20")
    if back.returncode == 0 and back.stdout.strip():
        return back.stdout.strip()
    root_commit = _g("rev-list", "--max-parents=0", "HEAD")
    first = root_commit.stdout.strip().splitlines()
    return first[0] if first else None


def run_bisect(root: Path | str, test_cmd: str, *, good: str, bad: str = "HEAD",
               timeout: int = 1200) -> BisectResult:
    """Drive ``git bisect run`` to find the first commit where ``test_cmd`` starts
    failing (non-zero exit). Runs in a throwaway LOCAL CLONE so your repo and
    working tree are never touched (and there are no linked-worktree bisect quirks)."""
    import shutil
    import tempfile

    from .worktree import is_git_repo

    if not is_git_repo(root):
        return BisectResult(None, note="not a git repository")

    tmp = tempfile.mkdtemp(prefix="ronin-bisect-")
    clone = str(Path(tmp) / "repo")

    def _g(*args, **kw):
        return subprocess.run(["git", "-C", clone, *args], capture_output=True,
                              text=True, timeout=kw.get("timeout", 60))
    try:
        cl = subprocess.run(["git", "clone", "--local", "--quiet",
                             str(Path(root).resolve()), clone],
                            capture_output=True, text=True, timeout=120)
        if cl.returncode != 0:
            return BisectResult(None, note=_git_note("clone failed.", cl))
        start = _g("bisect", "start", bad, good)
        if start.returncode != 0:
            return BisectResult(None, note=_git_note("bisect start failed.", start))
        # cwd = the clone so the oracle command reads each checked-out commit's files.
        run = subprocess.run(["git", "-C", clone, "bisect", "run", "sh", "-c", test_cmd],
                             cwd=clone, capture_output=True, text=True, timeout=timeout)
        sha = parse_first_bad(run.stdout + "\n" + run.stderr)
        if not sha:
            return BisectResult(None, note=_no_culprit_note(run))
        subject = _g("log", "-1", "--pretty=%h %s", sha).stdout.strip()
        diff = _g("show", "--no-color", sha).stdout
        return BisectResult(culprit=sha, subject=subject, diff=diff)
    except subprocess.TimeoutExpired:
        return BisectResult(None, note="bisect timed out")
    except Exception as e:  # noqa: BLE001
        return BisectResult(None, note=str(e)[:160])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
