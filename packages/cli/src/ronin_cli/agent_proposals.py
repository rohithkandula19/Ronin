"""Durable, explicitly applied patches produced by isolated agent worktrees.

Write orchestration intentionally runs outside the caller's checkout.  Once a
temporary worktree is removed, its patch should remain reviewable without
turning into an implicit merge.  This module archives role-attributed patches
under ``.ronin/agent-proposals`` and applies them only after strict local Git
preflight checks.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_MANIFEST = "proposal.json"
_RUNTIME_PATHS = (
    ".ronin/agent-proposals/",
    ".ronin/agent-runs/",
    ".ronin/autonomy-ledger.jsonl",
    ".ronin/provider-health.json",
)


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe(value: str, *, label: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid proposal {label}")
    return value


def _git(
    root: Path, *args: str, text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], input=text, capture_output=True,
        text=True, check=False,
    )


def git_head(root: Path | str) -> str | None:
    """Return the resolved HEAD revision, or ``None`` outside a usable repository."""
    try:
        result = _git(Path(root).resolve(), "rev-parse", "HEAD")
    except OSError:
        return None
    revision = result.stdout.strip().lower()
    return revision if result.returncode == 0 and _GIT_REVISION.fullmatch(revision) else None


@dataclass(frozen=True)
class ProposalPatch:
    role: str
    filename: str
    digest: str
    bytes: int


@dataclass(frozen=True)
class AgentProposal:
    run_id: str
    created: str
    base_revision: str
    verified: bool
    patches: tuple[ProposalPatch, ...]
    failed_subtasks: int = 0

    @property
    def status(self) -> str:
        return "ready" if self.verified else "blocked"

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["patches"] = [asdict(patch) for patch in self.patches]
        data["status"] = self.status
        return data

    @classmethod
    def from_dict(cls, raw: object) -> "AgentProposal | None":
        if not isinstance(raw, dict):
            return None
        run_id = raw.get("run_id")
        created = raw.get("created")
        base_revision = raw.get("base_revision")
        verified = raw.get("verified")
        patches = raw.get("patches")
        if not all(isinstance(value, str) for value in (run_id, created, base_revision)):
            return None
        if (
            not _SAFE_ID.fullmatch(run_id)
            or not _GIT_REVISION.fullmatch(base_revision)
            or not isinstance(verified, bool)
        ):
            return None
        if not isinstance(patches, list) or not patches:
            return None
        parsed: list[ProposalPatch] = []
        for item in patches:
            if not isinstance(item, dict):
                return None
            role = item.get("role")
            filename = item.get("filename")
            digest = item.get("digest")
            size = item.get("bytes")
            if (
                not isinstance(role, str) or not _SAFE_ID.fullmatch(role)
                or not isinstance(filename, str) or filename != f"{role}.patch"
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(size, int) or size < 1
            ):
                return None
            parsed.append(ProposalPatch(role, filename, digest, size))
        failed = raw.get("failed_subtasks", 0)
        return cls(run_id, created, base_revision, verified, tuple(parsed), max(0, int(failed or 0)))


@dataclass(frozen=True)
class ProposalApplication:
    run_id: str
    roles: tuple[str, ...]
    base_revision: str


class AgentProposalStore:
    """Store and explicitly stage isolated-worktree patches for one project."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / ".ronin" / "agent-proposals"

    def record(
        self,
        *,
        run_id: str,
        base_revision: str,
        diffs: Mapping[str, str],
        success: bool,
        subtask_results: Iterable[Mapping[str, object]],
    ) -> AgentProposal:
        """Archive nonempty role diffs and mark them eligible only after success.

        Recording a failed run is useful for forensic review, but its proposal is
        permanently blocked from this apply path.  It can still be inspected or
        manually adapted by a developer.
        """
        safe_run = _safe(run_id, label="run id")
        revision = base_revision.lower()
        if not _GIT_REVISION.fullmatch(revision):
            raise ValueError("proposal needs the source worktree's Git revision")
        entries = [
            (_safe(role, label="role"), diff)
            for role, diff in diffs.items()
            if diff.strip()
        ]
        if not entries:
            raise ValueError("proposal needs at least one non-empty worktree diff")
        failed = sum(not bool(result.get("success", False)) for result in subtask_results)
        proposal = AgentProposal(
            run_id=safe_run,
            created=_now(),
            base_revision=revision,
            verified=bool(success) and failed == 0,
            patches=tuple(
                ProposalPatch(role, f"{role}.patch", _digest(diff), len(diff.encode("utf-8")))
                for role, diff in entries
            ),
            failed_subtasks=failed,
        )
        target = self.directory / safe_run
        target.mkdir(parents=True, exist_ok=True)
        for patch, (_role, diff) in zip(proposal.patches, entries, strict=True):
            self._atomic_write(target / patch.filename, diff)
        self._atomic_write(
            target / _MANIFEST,
            json.dumps(proposal.as_dict(), indent=2, sort_keys=True) + "\n",
        )
        return proposal

    def list(self) -> tuple[AgentProposal, ...]:
        try:
            directories = sorted(path for path in self.directory.iterdir() if path.is_dir())
        except OSError:
            return ()
        rows = [
            proposal for path in directories
            if (proposal := self._read(path)) is not None
        ]
        return tuple(sorted(rows, key=lambda proposal: (proposal.created, proposal.run_id), reverse=True))

    def load(self, run_id: str) -> AgentProposal:
        proposal = self._read(self._path_for(run_id))
        if proposal is None:
            raise ValueError(
                f"agent proposal {Path(run_id).name!r} was not found or is invalid"
            )
        return proposal

    def read_patch(self, run_id: str, role: str) -> str:
        proposal = self.load(run_id)
        safe_role = _safe(role, label="role")
        patch = next((item for item in proposal.patches if item.role == safe_role), None)
        if patch is None:
            raise ValueError(
                f"proposal {proposal.run_id!r} has no patch for role {safe_role!r}"
            )
        return self._verified_patch(proposal, patch)

    def apply(self, run_id: str, *, roles: Iterable[str] | None = None) -> ProposalApplication:
        """Stage a verified, unmodified proposal only into a clean matching tree.

        This invokes ``git apply --index`` after a no-write `--check` pass.  It
        never creates a commit, force-applies hunks, or attempts a three-way
        merge.  A changed HEAD or any existing worktree/index changes require
        an operator to resolve the situation first.
        """
        proposal = self.load(run_id)
        if not proposal.verified:
            raise ValueError("proposal is blocked because its source run was not fully verified")
        current = git_head(self.root)
        if current is None:
            raise ValueError(
                "proposal application requires a Git repository with an initial commit"
            )
        if current != proposal.base_revision:
            raise ValueError(
                "proposal is stale: current HEAD does not match its source revision; rerun or rebase manually"
            )
        try:
            status = _git(self.root, "status", "--porcelain", "--untracked-files=all")
        except OSError as exc:
            raise ValueError(f"could not inspect Git worktree: {exc}") from exc
        if status.returncode != 0:
            raise ValueError(status.stderr.strip() or "could not inspect Git worktree")
        if _has_user_changes(status.stdout):
            raise ValueError("proposal application requires a clean working tree and index")

        selected = tuple(_safe(role, label="role") for role in roles) if roles else tuple(
            patch.role for patch in proposal.patches
        )
        if not selected:
            raise ValueError("select at least one proposal role")
        if len(set(selected)) != len(selected):
            raise ValueError("proposal roles must not be repeated")
        by_role = {patch.role: patch for patch in proposal.patches}
        unknown = sorted(set(selected) - set(by_role))
        if unknown:
            raise ValueError(f"proposal has no patch for role(s): {', '.join(unknown)}")
        payload = "\n".join(
            self._verified_patch(proposal, by_role[role]) for role in selected
        )
        try:
            check = _git(self.root, "apply", "--check", "--index", "-", text=payload)
        except OSError as exc:
            raise ValueError(f"Git apply check could not start: {exc}") from exc
        if check.returncode != 0:
            raise ValueError(check.stderr.strip() or "proposal cannot be applied cleanly")
        applied = _git(self.root, "apply", "--index", "-", text=payload)
        if applied.returncode != 0:
            raise ValueError(applied.stderr.strip() or "Git refused to stage the proposal")
        return ProposalApplication(proposal.run_id, selected, proposal.base_revision)

    def _path_for(self, run_id: str) -> Path:
        return self.directory / _safe(Path(run_id).name, label="run id")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(temporary, path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _read(self, directory: Path) -> AgentProposal | None:
        try:
            raw = json.loads((directory / _MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return AgentProposal.from_dict(raw)

    def _verified_patch(self, proposal: AgentProposal, patch: ProposalPatch) -> str:
        path = self._path_for(proposal.run_id) / patch.filename
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"proposal patch for {patch.role!r} is unavailable: {exc}"
            ) from exc
        if len(content.encode("utf-8")) != patch.bytes or _digest(content) != patch.digest:
            raise ValueError(f"proposal patch for {patch.role!r} was changed after recording")
        return content


def _has_user_changes(porcelain: str) -> bool:
    """Ignore only generated Ronin metadata when enforcing a clean checkout.

    A project may intentionally track a `.ronin` constitution or agent manifest,
    so this does not blanket-ignore that directory.  It skips the small set of
    runtime records this feature creates after the agent's isolated work has
    already completed.
    """
    for line in porcelain.splitlines():
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if not path.startswith(_RUNTIME_PATHS):
            return True
    return False
