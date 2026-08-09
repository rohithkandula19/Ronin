"""The task model, its loader, and the copy that puts a task in a temp workspace.

A task is a directory holding ``task.toml``, a ``fixture/`` the agent works in, and
— alongside it, never inside the workspace — a ``solution/`` holding the reference
answer. Three properties of this module are load-bearing.

**A malformed task names the file and the key.** The loader is the first thing a
person hits after writing a fixture by hand, and ``KeyError: 'prompt'`` with no path
is the worst possible answer: sixty task directories look identical in a traceback.
Every read goes through a helper that raises :class:`TaskError` carrying the path,
the key, and what was expected instead.

**The solution never reaches the workspace.** ``solution/`` is the answer key. If it
were copied, every pass rate the suite reports would be meaningless and nothing would
notice — the agent would simply read the answer. :data:`EXCLUDED_FROM_WORKSPACE`
filters by *name at any depth*, not just at the top level, because "the copy skipped
the sibling directory" is an accident of layout and not a guarantee.

**A task that needs the network is skipped, not attempted.** A ``git_sha`` task
describes a repository to clone, and cloning is deliberately not implemented here:
the suite must pass offline with no API keys, so :func:`materialize` raises
:class:`NeedsNetwork` and the runner turns that into a *skipped* result carrying the
reason. A silent local fallback would report a pass rate over a suite that never ran.

Unknown keys in ``task.toml`` are collected into :attr:`EvalTask.extra_keys` rather
than rejected. The fixture author and this loader ship separately, and a loader that
refuses a key it has not heard of makes every schema addition a hard breakage of the
whole suite; a loader that records them makes it a note.
"""
from __future__ import annotations

import shutil
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

#: Where the suite lives, relative to the repo root. A default, not a hard-coding:
#: every entry point takes the root as an argument.
SUITE_RELATIVE_ROOT: Final = "tests/evals"

TASK_FILENAME: Final = "task.toml"
MANIFEST_FILENAME: Final = "manifest.toml"
FIXTURE_DIRNAME: Final = "fixture"
SOLUTION_DIRNAME: Final = "solution"
VERIFY_FILENAME: Final = "verify.sh"

#: Never copied into a workspace, matched by name at any depth. ``solution`` is the
#: reason this set exists; the caches are here because copying them wastes time and
#: a stale ``__pycache__`` can make an imported module disagree with its source.
EXCLUDED_FROM_WORKSPACE: Final[frozenset[str]] = frozenset(
    {SOLUTION_DIRNAME, "__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
)

#: Used when ``task.toml`` does not say. Both are ceilings a task may lower, and
#: neither is a measurement — they are the caps a runaway task is stopped by.
DEFAULT_MAX_TURNS: Final = 25
DEFAULT_TIMEOUT_SECONDS: Final = 300.0

#: The keys this loader knows. Anything else lands in ``extra_keys``.
KNOWN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "category",
        "prompt",
        "fixture",
        "git_sha",
        "git_url",
        "files_expected",
        "max_turns",
        "timeout_seconds",
        "tags",
        "regression_gate",
    }
)


class TaskError(ValueError):
    """A ``task.toml`` that cannot be read, naming the file and the offending key.

    Carries the parts separately as well as in the message so a loader over sixty
    fixtures can group failures by key instead of by string.
    """

    def __init__(self, path: Path, key: str, detail: str) -> None:
        super().__init__(f"{path}: key {key!r} — {detail}")
        self.path = path
        self.key = key
        self.detail = detail


class ManifestMismatch(ValueError):
    """The manifest and the tree disagree, with both sides named.

    Either direction is an error. A task on disk but not in the manifest runs in
    nobody's pass rate; a task in the manifest but not on disk means someone deleted
    a fixture and the suite silently got easier.
    """

    def __init__(
        self,
        *,
        manifest_path: Path,
        suite_root: Path,
        missing_from_manifest: Sequence[str],
        missing_from_disk: Sequence[str],
    ) -> None:
        parts = [f"{manifest_path} disagrees with the tree under {suite_root}"]
        if missing_from_manifest:
            parts.append(
                f"on disk but not in {manifest_path.name}: "
                f"{', '.join(sorted(missing_from_manifest))}"
            )
        if missing_from_disk:
            parts.append(
                f"in {manifest_path.name} but not on disk: "
                f"{', '.join(sorted(missing_from_disk))}"
            )
        super().__init__("; ".join(parts))
        self.manifest_path = manifest_path
        self.suite_root = suite_root
        self.missing_from_manifest = tuple(missing_from_manifest)
        self.missing_from_disk = tuple(missing_from_disk)


class NeedsNetwork(RuntimeError):
    """A task whose fixture is a git revision, which this runner will not fetch.

    Raised rather than quietly degraded: the runner converts it into a skipped
    result naming the url, so a report over a partly-skipped suite says so instead
    of reporting a pass rate as if every task had run.
    """

    def __init__(self, task_id: str, git_url: str, git_sha: str) -> None:
        super().__init__(
            f"task {task_id!r} needs {git_url}@{git_sha}, and this runner does not "
            "clone: the suite is required to run offline with no credentials. Fetch "
            "the revision yourself and point the task at a local fixture, or run it "
            "outside the offline gate."
        )
        self.task_id = task_id
        self.git_url = git_url
        self.git_sha = git_sha


# --------------------------------------------------------------------------- #
# Typed readers — every one of them names the file and the key on failure
# --------------------------------------------------------------------------- #


def _require(data: Mapping[str, Any], key: str, path: Path) -> Any:
    if key not in data:
        raise TaskError(path, key, "required and missing")
    return data[key]


def _as_str(value: object, key: str, path: Path, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TaskError(path, key, f"must be a string, got {type(value).__name__}")
    if not value and not allow_empty:
        raise TaskError(path, key, "must be a non-empty string")
    return value


def _optional_str(data: Mapping[str, Any], key: str, path: Path) -> str | None:
    """An optional string key, where an empty string means absent.

    TOML cannot express null, so ``git_sha = ""`` is how a fixture says "this is not a
    git task". Rejecting it would fail the whole suite over the format's limitation.
    """
    if key not in data:
        return None
    value = _as_str(data[key], key, path, allow_empty=True)
    return value or None


def _str_tuple(data: Mapping[str, Any], key: str, path: Path) -> tuple[str, ...]:
    raw = data.get(key, ())
    if isinstance(raw, str):
        raise TaskError(path, key, "must be a list of strings, not a bare string")
    if not isinstance(raw, (list, tuple)):
        raise TaskError(path, key, f"must be a list of strings, got {type(raw).__name__}")
    return tuple(_as_str(item, key, path) for item in raw)


def _as_int(data: Mapping[str, Any], key: str, path: Path, default: int) -> int:
    if key not in data:
        return default
    raw = data[key]
    # bool is an int in Python, and `max_turns = true` is a typo, not a count.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TaskError(path, key, f"must be an integer, got {type(raw).__name__}")
    if raw <= 0:
        raise TaskError(path, key, f"must be positive, got {raw}")
    return raw


def _as_float(data: Mapping[str, Any], key: str, path: Path, default: float) -> float:
    if key not in data:
        return default
    raw = data[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TaskError(path, key, f"must be a number, got {type(raw).__name__}")
    if raw <= 0:
        raise TaskError(path, key, f"must be positive, got {raw}")
    return float(raw)


def _as_bool(data: Mapping[str, Any], key: str, path: Path, default: bool) -> bool:
    if key not in data:
        return default
    raw = data[key]
    if not isinstance(raw, bool):
        raise TaskError(path, key, f"must be true or false, got {type(raw).__name__}")
    return raw


# --------------------------------------------------------------------------- #
# The task
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EvalTask:
    """One eval task, as declared on disk.

    ``root`` is the directory ``task.toml`` was read from, so every path this task
    refers to is resolvable without carrying the suite root around separately.
    """

    id: str
    category: str
    prompt: str
    root: Path
    fixture: str | None = None
    git_sha: str | None = None
    git_url: str | None = None
    files_expected: tuple[str, ...] = ()
    max_turns: int = DEFAULT_MAX_TURNS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    tags: tuple[str, ...] = ()
    regression_gate: bool = False
    #: Keys present in ``task.toml`` that this loader does not know. Not an error
    #: (see the module docstring), but reported so a typo is visible.
    extra_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("EvalTask.id is required")
        if not self.category:
            raise ValueError("EvalTask.category is required")

    @property
    def needs_network(self) -> bool:
        """Whether materializing this task would require a fetch."""
        return bool(self.git_sha or self.git_url)

    @property
    def fixture_dir(self) -> Path | None:
        """The directory copied into the workspace, if this task ships one."""
        if self.fixture is None:
            candidate = self.root / FIXTURE_DIRNAME
            return candidate if candidate.is_dir() else None
        candidate = self.root / self.fixture
        return candidate if candidate.is_dir() else None

    @property
    def solution_dir(self) -> Path | None:
        """The reference answer. Present for a human; never copied anywhere."""
        candidate = self.root / SOLUTION_DIRNAME
        return candidate if candidate.is_dir() else None

    @property
    def verify_source(self) -> Path | None:
        """Where ``verify.sh`` lives on disk, whichever of the two layouts is used.

        Looked for inside the fixture first and then beside ``task.toml``. Both
        layouts are reasonable — inside the fixture the script travels with the code
        it checks; beside ``task.toml`` it stays out of the agent's way — so the
        loader accepts either rather than making sixty fixtures agree with a guess.
        """
        fixture = self.fixture_dir
        if fixture is not None and (fixture / VERIFY_FILENAME).is_file():
            return fixture / VERIFY_FILENAME
        beside = self.root / VERIFY_FILENAME
        return beside if beside.is_file() else None

    @property
    def timeout(self) -> float:
        return self.timeout_seconds


def load_task(path: str | Path) -> EvalTask:
    """Read one ``task.toml``. ``path`` may be the file or its directory."""
    candidate = Path(path)
    toml_path = candidate / TASK_FILENAME if candidate.is_dir() else candidate
    if not toml_path.is_file():
        raise FileNotFoundError(f"no {TASK_FILENAME} at {toml_path}")

    try:
        with toml_path.open("rb") as handle:
            data: Mapping[str, Any] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise TaskError(toml_path, "<file>", f"is not valid TOML: {exc}") from exc

    task_id = _as_str(_require(data, "id", toml_path), "id", toml_path)
    category = _as_str(_require(data, "category", toml_path), "category", toml_path)
    prompt = _as_str(_require(data, "prompt", toml_path), "prompt", toml_path)

    # TOML has no null, so a fixture author writes `git_sha = ""` for "not a git task".
    # Treating that as malformed would reject the whole suite over a spelling of
    # absence the format forces on them.
    fixture = _optional_str(data, "fixture", toml_path)
    git_sha = _optional_str(data, "git_sha", toml_path)
    git_url = _optional_str(data, "git_url", toml_path)
    if bool(git_sha) != bool(git_url):
        missing = "git_url" if git_sha else "git_sha"
        raise TaskError(
            toml_path,
            missing,
            "git_sha and git_url are one unit: a revision with no repository (or a "
            "repository with no pinned revision) is not reproducible",
        )

    return EvalTask(
        id=task_id,
        category=category,
        prompt=prompt,
        root=toml_path.parent,
        fixture=fixture,
        git_sha=git_sha,
        git_url=git_url,
        files_expected=_str_tuple(data, "files_expected", toml_path),
        max_turns=_as_int(data, "max_turns", toml_path, DEFAULT_MAX_TURNS),
        timeout_seconds=_as_float(
            data, "timeout_seconds", toml_path, DEFAULT_TIMEOUT_SECONDS
        ),
        tags=_str_tuple(data, "tags", toml_path),
        regression_gate=_as_bool(data, "regression_gate", toml_path, False),
        extra_keys=tuple(sorted(set(data) - KNOWN_KEYS)),
    )


def task_paths(root: str | Path) -> tuple[Path, ...]:
    """Every ``task.toml`` under ``root``, sorted, ignoring fixture interiors.

    A ``task.toml`` *inside* a ``fixture/`` or ``solution/`` belongs to the fixture's
    own contents (a task whose subject is an eval suite is a real case), not to this
    suite, so it is skipped rather than loaded as a sibling task.
    """
    base = Path(root)
    if not base.is_dir():
        return ()
    found: list[Path] = []
    for path in sorted(base.rglob(TASK_FILENAME)):
        parts = set(path.relative_to(base).parts[:-1])
        if parts & {FIXTURE_DIRNAME, SOLUTION_DIRNAME} or parts & EXCLUDED_FROM_WORKSPACE:
            continue
        found.append(path)
    return tuple(found)


def load_tasks(root: str | Path) -> tuple[EvalTask, ...]:
    """Every task under ``root``, ordered by ``(category, id)``.

    The order is part of the contract: a report whose rows move between two runs of
    the same suite cannot be diffed, and diffing two runs is the whole point.
    """
    tasks = [load_task(path) for path in task_paths(root)]
    seen: dict[str, Path] = {}
    for task in tasks:
        if task.id in seen:
            raise TaskError(
                task.root / TASK_FILENAME,
                "id",
                f"duplicate task id {task.id!r}, already used by {seen[task.id]}",
            )
        seen[task.id] = task.root / TASK_FILENAME
    return tuple(sorted(tasks, key=lambda task: (task.category, task.id)))


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

#: Keys a manifest may carry the id list under. Several spellings are accepted
#: because the manifest is hand-written and the cost of guessing wrong is the whole
#: suite refusing to load; the *contents* are still checked strictly.
MANIFEST_ID_KEYS: Final[tuple[str, ...]] = ("tasks", "task_ids", "ids")


@dataclass(frozen=True, slots=True)
class Manifest:
    """The declared contents of the suite: which task ids are supposed to exist."""

    path: Path
    task_ids: tuple[str, ...]
    raw: Mapping[str, Any]

    @property
    def count(self) -> int:
        return len(self.task_ids)


def load_manifest(root: str | Path) -> Manifest:
    """Read ``manifest.toml`` from the suite root.

    Accepts either a list of id strings or a list of tables carrying ``id``, under
    any of :data:`MANIFEST_ID_KEYS`.
    """
    base = Path(root)
    path = base / MANIFEST_FILENAME if base.is_dir() else base
    if not path.is_file():
        raise FileNotFoundError(
            f"no {MANIFEST_FILENAME} at {path} — the manifest is what makes a missing "
            "fixture detectable, so the suite is not loadable without it"
        )
    try:
        with path.open("rb") as handle:
            data: Mapping[str, Any] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise TaskError(path, "<file>", f"is not valid TOML: {exc}") from exc

    key = next((name for name in MANIFEST_ID_KEYS if name in data), None)
    if key is None:
        raise TaskError(
            path,
            MANIFEST_ID_KEYS[0],
            f"missing — expected one of {', '.join(MANIFEST_ID_KEYS)} listing the "
            "task ids the suite is supposed to contain",
        )
    raw_ids = data[key]
    if not isinstance(raw_ids, (list, tuple)):
        raise TaskError(path, key, f"must be a list, got {type(raw_ids).__name__}")

    ids: list[str] = []
    for entry in raw_ids:
        if isinstance(entry, str):
            ids.append(_as_str(entry, key, path))
        elif isinstance(entry, Mapping):
            if "id" not in entry:
                raise TaskError(path, key, "a table entry must carry an 'id' key")
            ids.append(_as_str(entry["id"], f"{key}[].id", path))
        else:
            raise TaskError(
                path, key, f"entries must be strings or tables, got {type(entry).__name__}"
            )
    duplicates = sorted({name for name in ids if ids.count(name) > 1})
    if duplicates:
        raise TaskError(path, key, f"duplicate ids: {', '.join(duplicates)}")
    return Manifest(path=path, task_ids=tuple(ids), raw=data)


def check_manifest(
    manifest: Manifest, tasks: Sequence[EvalTask], *, suite_root: str | Path
) -> None:
    """Raise :class:`ManifestMismatch` unless the manifest and the tree agree."""
    on_disk = {task.id for task in tasks}
    declared = set(manifest.task_ids)
    missing_from_manifest = sorted(on_disk - declared)
    missing_from_disk = sorted(declared - on_disk)
    if missing_from_manifest or missing_from_disk:
        raise ManifestMismatch(
            manifest_path=manifest.path,
            suite_root=Path(suite_root),
            missing_from_manifest=missing_from_manifest,
            missing_from_disk=missing_from_disk,
        )


def load_suite(root: str | Path, *, require_manifest: bool = True) -> tuple[EvalTask, ...]:
    """Load every task and check it against the manifest.

    ``require_manifest=False`` exists for a partly-written suite (a fixture author
    adding tasks before updating the manifest) and is *not* the default: the default
    has to be the checked one, or the check is decoration.
    """
    tasks = load_tasks(root)
    try:
        manifest = load_manifest(root)
    except FileNotFoundError:
        if require_manifest:
            raise
        return tasks
    check_manifest(manifest, tasks, suite_root=root)
    return tasks


# --------------------------------------------------------------------------- #
# Materializing into a workspace
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Workspace:
    """A task's fixture, copied somewhere the agent may destroy it."""

    task_id: str
    root: Path
    verify_script: Path | None = None
    files: tuple[str, ...] = ()

    @property
    def verifiable(self) -> bool:
        return self.verify_script is not None


def _copy_tree(source: Path, dest: Path) -> list[str]:
    """Copy ``source`` into ``dest``, skipping excluded names at every depth."""
    copied: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        if entry.name in EXCLUDED_FROM_WORKSPACE:
            continue
        target = dest / entry.name
        if entry.is_dir() and not entry.is_symlink():
            copied.extend(
                f"{entry.name}/{child}" for child in _copy_tree(entry, target)
            )
        else:
            # follow_symlinks=False keeps a fixture's deliberate broken symlink
            # broken, which is the point of a fixture that tests one.
            shutil.copy2(entry, target, follow_symlinks=False)
            copied.append(entry.name)
    return copied


def materialize(task: EvalTask, dest: str | Path) -> Workspace:
    """Copy ``task``'s fixture into ``dest`` and return the workspace.

    Raises :class:`NeedsNetwork` for a ``git_sha`` task. A task with no fixture at
    all yields an empty workspace, which is correct for a "create this from nothing"
    task and is not the same thing as a missing fixture — a *named* fixture that does
    not exist raises :class:`TaskError`.
    """
    if task.needs_network:
        raise NeedsNetwork(task.id, task.git_url or "", task.git_sha or "")

    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)

    fixture = task.fixture_dir
    if fixture is None and task.fixture is not None:
        raise TaskError(
            task.root / TASK_FILENAME,
            "fixture",
            f"names {task.fixture!r}, which is not a directory under {task.root}",
        )

    files = _copy_tree(fixture, root) if fixture is not None else []

    verify_script: Path | None = None
    source = task.verify_source
    if source is not None:
        target = root / VERIFY_FILENAME
        if not target.exists():
            shutil.copy2(source, target, follow_symlinks=False)
            files.append(VERIFY_FILENAME)
        verify_script = target

    return Workspace(
        task_id=task.id,
        root=root,
        verify_script=verify_script,
        files=tuple(sorted(files)),
    )


__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TIMEOUT_SECONDS",
    "EXCLUDED_FROM_WORKSPACE",
    "FIXTURE_DIRNAME",
    "KNOWN_KEYS",
    "MANIFEST_FILENAME",
    "MANIFEST_ID_KEYS",
    "SOLUTION_DIRNAME",
    "SUITE_RELATIVE_ROOT",
    "TASK_FILENAME",
    "VERIFY_FILENAME",
    "EvalTask",
    "Manifest",
    "ManifestMismatch",
    "NeedsNetwork",
    "TaskError",
    "Workspace",
    "check_manifest",
    "load_manifest",
    "load_suite",
    "load_task",
    "load_tasks",
    "materialize",
    "task_paths",
]
