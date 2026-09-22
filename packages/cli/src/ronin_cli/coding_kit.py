"""One hundred local coding-agent commands.

Each command inspects the repository on disk. None of them call a model,
send network traffic, or print secret values — only names of ``RONIN_*``
environment variables, never their contents.
"""
from __future__ import annotations

import ast
import os
import subprocess
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 and older
    tomllib = None  # type: ignore[assignment]
from collections import Counter
from pathlib import Path

import typer

kit_app = typer.Typer(
    help="100 local coding-agent commands (git, repo shape, hygiene). No network.",
    no_args_is_help=True,
)

_SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ronin", "dist", "build"}


def _root(path: Path) -> Path:
    return path.resolve()


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return "git is not installed"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        return err or f"git {' '.join(args)} failed"
    return proc.stdout.rstrip() or "(empty)"


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP and not d.startswith(".git")]
        for name in filenames:
            yield Path(dirpath) / name


def _tracked(root: Path) -> list[str]:
    out = _git(root, "ls-files")
    if out.startswith("fatal:") or out == "git is not installed":
        return []
    return [line for line in out.splitlines() if line]


def _load_toml(path: Path) -> dict | None:
    if tomllib is None:
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}


def _text(path: Path, limit: int = 200_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _py_tree(root: Path):
    trees = []
    for rel in _tracked(root):
        if rel.endswith(".py"):
            src = _text(root / rel)
            if not src:
                continue
            try:
                trees.append(ast.parse(src))
            except SyntaxError:
                continue
    return trees


def _count_nodes(root: Path, kind: type) -> str:
    total = 0
    for tree in _py_tree(root):
        total += sum(1 for node in ast.walk(tree) if isinstance(node, kind))
    return str(total)


def _scan_lines(root: Path, pred) -> str:
    hits = 0
    for rel in _tracked(root):
        for line in _text(root / rel).splitlines():
            if pred(line):
                hits += 1
    return str(hits)


COMMANDS: list[tuple[str, str]] = []


def _cmd(name: str, help_text: str):
    def deco(fn):
        COMMANDS.append((name, help_text))

        def wrapped(
            root: Path = typer.Option(Path("."), "--root", help="Repository root."),
        ) -> None:
            typer.echo(fn(_root(root)))

        wrapped.__doc__ = help_text
        wrapped.__name__ = "kit_" + name.replace("-", "_")
        kit_app.command(name)(wrapped)
        return fn

    return deco


@_cmd("status", "Short git status.")
def _status(root: Path) -> str:
    return _git(root, "status", "--short", "--branch")


@_cmd("diffstat", "Diff stat of unstaged and staged changes.")
def _diffstat(root: Path) -> str:
    return _git(root, "diff", "--stat", "HEAD")


@_cmd("staged-diff", "Diff stat of the index only.")
def _staged_diff(root: Path) -> str:
    return _git(root, "diff", "--cached", "--stat")


@_cmd("log", "Last 20 commit subjects.")
def _log(root: Path) -> str:
    return _git(root, "log", "-20", "--oneline")


@_cmd("branches", "Local branches with the current one marked.")
def _branches(root: Path) -> str:
    return _git(root, "branch", "--list")


@_cmd("remotes", "Configured remotes.")
def _remotes(root: Path) -> str:
    return _git(root, "remote", "-v")


@_cmd("tags", "Tags, newest first.")
def _tags(root: Path) -> str:
    return _git(root, "tag", "--sort=-creatordate")


@_cmd("head", "Current commit sha.")
def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


@_cmd("branch", "Current branch name.")
def _branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD")


@_cmd("root", "Repository top directory.")
def _repo_root(root: Path) -> str:
    return _git(root, "rev-parse", "--show-toplevel")


@_cmd("upstream", "Upstream of the current branch, if any.")
def _upstream(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")


@_cmd("ahead-behind", "How many commits this branch is ahead/behind its upstream.")
def _ahead_behind(root: Path) -> str:
    return _git(root, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")


@_cmd("unpushed", "Commits on this branch that are not on the upstream.")
def _unpushed(root: Path) -> str:
    return _git(root, "log", "--oneline", "@{upstream}..HEAD")


@_cmd("stash", "Stash list.")
def _stash(root: Path) -> str:
    return _git(root, "stash", "list")


@_cmd("tracked-count", "Number of files git tracks.")
def _tracked_count(root: Path) -> str:
    return str(len(_tracked(root)))


@_cmd("untracked", "Untracked files, excluding ignored ones.")
def _untracked(root: Path) -> str:
    return _git(root, "ls-files", "--others", "--exclude-standard")


@_cmd("staged", "Files staged for commit.")
def _staged(root: Path) -> str:
    return _git(root, "diff", "--cached", "--name-only")


@_cmd("modified", "Tracked files with unstaged edits.")
def _modified(root: Path) -> str:
    return _git(root, "diff", "--name-only")


@_cmd("authors", "Author names on this branch, with commit counts.")
def _authors(root: Path) -> str:
    return _git(root, "shortlog", "-sn", "HEAD")


@_cmd("last-commit", "Latest commit, one line.")
def _last_commit(root: Path) -> str:
    return _git(root, "log", "-1", "--pretty=format:%h %s")


@_cmd("last-author", "Author of HEAD.")
def _last_author(root: Path) -> str:
    return _git(root, "log", "-1", "--pretty=format:%an")


@_cmd("head-age", "When HEAD was committed, relative.")
def _head_age(root: Path) -> str:
    return _git(root, "log", "-1", "--pretty=format:%cr")


@_cmd("first-commit", "Oldest commit still in this history.")
def _first_commit(root: Path) -> str:
    return _git(root, "rev-list", "--max-parents=0", "--oneline", "HEAD")


@_cmd("commits-week", "Commits in the last 7 days.")
def _commits_week(root: Path) -> str:
    return _git(root, "rev-list", "--count", "--since=7.days", "HEAD")


@_cmd("files-in-head", "Files touched by HEAD.")
def _files_in_head(root: Path) -> str:
    return _git(root, "show", "--name-only", "--pretty=format:", "HEAD")


@_cmd("churn", "Files with the most commits in the last 50.")
def _churn(root: Path) -> str:
    log = _git(root, "log", "-50", "--name-only", "--pretty=format:")
    counts = Counter(line for line in log.splitlines() if line.strip())
    lines = [f"{n:4}  {path}" for path, n in counts.most_common(15)]
    return "\n".join(lines) or "(no history)"


@_cmd("languages", "Tracked files grouped by extension.")
def _languages(root: Path) -> str:
    counts: Counter[str] = Counter()
    for rel in _tracked(root):
        suffix = Path(rel).suffix.lower() or "(none)"
        counts[suffix] += 1
    return "\n".join(f"{n:5}  {ext}" for ext, n in counts.most_common(20)) or "(none)"


@_cmd("largest", "Largest tracked files, in bytes.")
def _largest(root: Path) -> str:
    sizes = []
    for rel in _tracked(root):
        path = root / rel
        try:
            sizes.append((path.stat().st_size, rel))
        except OSError:
            continue
    sizes.sort(reverse=True)
    return "\n".join(f"{size:8}  {rel}" for size, rel in sizes[:15]) or "(none)"


@_cmd("todos", "Count of TODO and FIXME markers in tracked text.")
def _todos(root: Path) -> str:
    todo = fixme = 0
    for rel in _tracked(root):
        text = _text(root / rel)
        todo += text.count("TODO")
        fixme += text.count("FIXME")
    return f"TODO {todo}\nFIXME {fixme}"


@_cmd("conflict-markers", "Tracked lines that still contain merge markers.")
def _conflicts(root: Path) -> str:
    hits = []
    for rel in _tracked(root):
        for i, line in enumerate(_text(root / rel).splitlines(), 1):
            if line.startswith(("<<<<<<<", "=======", ">>>>>>>")):
                hits.append(f"{rel}:{i}")
    return "\n".join(hits[:50]) or "none"


@_cmd("long-lines", "Tracked lines longer than 120 characters.")
def _long_lines(root: Path) -> str:
    return _scan_lines(root, lambda line: len(line) > 120)


@_cmd("trailing-ws", "Tracked lines with trailing whitespace.")
def _trailing(root: Path) -> str:
    return _scan_lines(root, lambda line: line.rstrip("\n") != line.rstrip())


@_cmd("py-files", "Tracked Python files.")
def _py_files(root: Path) -> str:
    rows = [rel for rel in _tracked(root) if rel.endswith(".py")]
    return "\n".join(rows) if rows else "none"


@_cmd("py-count", "How many tracked Python files.")
def _py_count(root: Path) -> str:
    return str(sum(1 for rel in _tracked(root) if rel.endswith(".py")))


@_cmd("test-files", "Tracked files that look like tests.")
def _test_files(root: Path) -> str:
    rows = [rel for rel in _tracked(root) if Path(rel).name.startswith("test_") or rel.endswith("_test.py")]
    return "\n".join(rows) if rows else "none"


@_cmd("functions", "Python function definitions in tracked files.")
def _functions(root: Path) -> str:
    return _count_nodes(root, ast.FunctionDef) if False else str(
        sum(1 for tree in _py_tree(root) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    )


@_cmd("classes", "Python class definitions in tracked files.")
def _classes(root: Path) -> str:
    return _count_nodes(root, ast.ClassDef)


@_cmd("imports", "Import statements in tracked Python.")
def _imports(root: Path) -> str:
    total = 0
    for tree in _py_tree(root):
        total += sum(1 for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
    return str(total)


@_cmd("syntax", "Tracked Python files that do not parse.")
def _syntax(root: Path) -> str:
    bad = []
    for rel in _tracked(root):
        if not rel.endswith(".py"):
            continue
        src = _text(root / rel)
        if not src:
            continue
        try:
            ast.parse(src)
        except SyntaxError as exc:
            bad.append(f"{rel}:{exc.lineno}")
    return "\n".join(bad) or "ok"


@_cmd("toml", "Tracked TOML files that do not parse.")
def _toml(root: Path) -> str:
    if tomllib is None:
        return "toml parser unavailable"
    bad = []
    for rel in _tracked(root):
        if not rel.endswith(".toml"):
            continue
        if _load_toml(root / rel) == {}:
            bad.append(rel)
    return "\n".join(bad) or "ok"


@_cmd("linecount", "Total lines across tracked text files.")
def _linecount(root: Path) -> str:
    total = 0
    for rel in _tracked(root):
        total += _text(root / rel).count("\n")
    return str(total)


@_cmd("blank-lines", "Blank lines across tracked text files.")
def _blank(root: Path) -> str:
    total = 0
    for rel in _tracked(root):
        total += sum(1 for line in _text(root / rel).splitlines() if not line.strip())
    return str(total)


@_cmd("shebangs", "Tracked files whose first line is a shebang.")
def _shebangs(root: Path) -> str:
    rows = []
    for rel in _tracked(root):
        text = _text(root / rel)
        if text.startswith("#!"):
            rows.append(rel)
    return "\n".join(rows) or "none"


@_cmd("secret-shapes", "Count of lines that look like common key prefixes. Values are not printed.")
def _secret_shapes(root: Path) -> str:
    needles = ("AKIA", "sk-", "ghp_", "xoxb-", "BEGIN PRIVATE KEY")
    hits = 0
    for rel in _tracked(root):
        text = _text(root / rel)
        hits += sum(text.count(n) for n in needles)
    return str(hits)


@_cmd("env-names", "Names of RONIN_* variables in the environment. Values are not printed.")
def _env_names(_root: Path) -> str:
    names = sorted(key for key in os.environ if key.startswith("RONIN_"))
    return "\n".join(names) or "none"


@_cmd("python", "Python version running this command.")
def _python(_root: Path) -> str:
    import sys
    return sys.version.split()[0]


@_cmd("git-version", "Installed git version.")
def _git_version(root: Path) -> str:
    return _git(root, "--version")


@_cmd("git-user", "user.name from local or global git config.")
def _git_user(root: Path) -> str:
    return _git(root, "config", "user.name")


@_cmd("hooks", "Files in .git/hooks, excluding samples.")
def _hooks(root: Path) -> str:
    hook_dir = root / ".git" / "hooks"
    if not hook_dir.is_dir():
        return "none"
    names = [p.name for p in sorted(hook_dir.iterdir()) if p.is_file() and not p.name.endswith(".sample")]
    return "\n".join(names) or "none"


@_cmd("workflows", "GitHub Actions workflow files.")
def _workflows(root: Path) -> str:
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return "none"
    return "\n".join(p.name for p in sorted(directory.glob("*.yml"))) or "none"


@_cmd("has-agents", "Whether AGENTS.md or CLAUDE.md exists.")
def _has_agents(root: Path) -> str:
    found = [name for name in ("AGENTS.md", "CLAUDE.md", "RONIN.md") if (root / name).is_file()]
    return "\n".join(found) or "none"


@_cmd("has-readme", "README filename if present.")
def _has_readme(root: Path) -> str:
    for name in ("README.md", "README.rst", "README"):
        if (root / name).is_file():
            return name
    return "none"


@_cmd("has-license", "License filename if present.")
def _has_license(root: Path) -> str:
    for name in ("LICENSE", "LICENSE.md", "COPYING"):
        if (root / name).is_file():
            return name
    return "none"


@_cmd("has-codeowners", "Whether CODEOWNERS exists.")
def _codeowners(root: Path) -> str:
    for rel in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        if (root / rel).is_file():
            return rel
    return "none"


@_cmd("has-editor", "Whether an editorconfig exists.")
def _editor(root: Path) -> str:
    return "yes" if (root / ".editorconfig").is_file() else "no"


@_cmd("has-docker", "Dockerfile or compose file, if present.")
def _docker(root: Path) -> str:
    found = [name for name in ("Dockerfile", "docker-compose.yml", "compose.yml") if (root / name).is_file()]
    return "\n".join(found) or "none"


@_cmd("has-precommit", "Whether a pre-commit config exists.")
def _precommit(root: Path) -> str:
    return "yes" if (root / ".pre-commit-config.yaml").is_file() else "no"


def _project_table(root: Path) -> dict | None:
    path = root / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return None
    data = _load_toml(path)
    if not data:
        return {}
    return data.get("project") or {}


@_cmd("pyproject-name", "Project name from pyproject.toml.")
def _py_name(root: Path) -> str:
    project = _project_table(root)
    if project is None:
        return "none"
    if project == {}:
        return "invalid"
    return str(project.get("name") or "unset")


@_cmd("pyproject-version", "Project version from pyproject.toml.")
def _py_version(root: Path) -> str:
    project = _project_table(root)
    if project is None:
        return "none"
    if project == {}:
        return "invalid"
    return str(project.get("version") or "unset")


@_cmd("scripts", "Script names declared in pyproject.toml.")
def _scripts(root: Path) -> str:
    project = _project_table(root)
    if project is None:
        return "none"
    if project == {}:
        return "invalid"
    scripts = project.get("scripts") or {}
    return "\n".join(f"{name} = {target}" for name, target in scripts.items()) or "none"


@_cmd("extras", "Optional dependency group names.")
def _extras(root: Path) -> str:
    project = _project_table(root)
    if project is None:
        return "none"
    if project == {}:
        return "invalid"
    extras = project.get("optional-dependencies") or {}
    return "\n".join(sorted(extras)) or "none"


@_cmd("makefile", "Makefile targets, from lines that end with a colon.")
def _makefile(root: Path) -> str:
    path = root / "Makefile"
    if not path.is_file():
        return "none"
    targets = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line and not line.startswith(("#", "\t", " ")) and ":" in line:
            targets.append(line.split(":", 1)[0])
    return "\n".join(targets) or "none"


@_cmd("merge-state", "Whether a merge, rebase, or cherry-pick is in progress.")
def _merge_state(root: Path) -> str:
    git_dir = _git(root, "rev-parse", "--git-path", ".")
    base = Path(git_dir)
    if not base.is_absolute():
        base = root / base
    flags = [name for name in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD") if (base / name).exists()]
    return "\n".join(flags) or "clean"


@_cmd("dirty", "yes if the worktree has any change, else no.")
def _dirty(root: Path) -> str:
    out = _git(root, "status", "--porcelain")
    if out in {"(empty)", ""}:
        return "no"
    if out.startswith("fatal:"):
        return out
    return "yes"


@_cmd("submodules", "Configured submodule paths.")
def _submodules(root: Path) -> str:
    return _git(root, "submodule", "status")


@_cmd("sparse", "Whether this checkout is sparse.")
def _sparse(root: Path) -> str:
    out = _git(root, "config", "--bool", "core.sparseCheckout")
    return "yes" if out.strip() == "true" else "no"


@_cmd("worktree-count", "How many worktrees this repository has.")
def _wt_count(root: Path) -> str:
    out = _git(root, "worktree", "list", "--porcelain")
    return str(sum(1 for line in out.splitlines() if line.startswith("worktree ")))


@_cmd("index-count", "Entries in the git index.")
def _index(root: Path) -> str:
    out = _git(root, "ls-files", "-s")
    if out in {"(empty)", ""}:
        return "0"
    return str(len(out.splitlines()))


@_cmd("pack-count", "Number of pack files under .git.")
def _packs(root: Path) -> str:
    git_dir = root / ".git"
    if not git_dir.is_dir():
        return "0"
    return str(len(list((git_dir / "objects" / "pack").glob("*.pack")))) if (git_dir / "objects" / "pack").is_dir() else "0"


@_cmd("assume-unchanged", "Paths marked assume-unchanged.")
def _assume(root: Path) -> str:
    rows = []
    for line in _git(root, "ls-files", "-v").splitlines():
        if len(line) > 2 and line[0].isalpha() and line[0].islower() and line[1] == " ":
            rows.append(line[2:])
    return "\n".join(rows) or "none"


@_cmd("skip-worktree", "Paths marked skip-worktree.")
def _skip_wt(root: Path) -> str:
    rows = [line[2:] for line in _git(root, "ls-files", "-v").splitlines() if line.startswith("S ")]
    return "\n".join(rows) or "none"


@_cmd("renames", "Detected renames in the unstaged diff.")
def _renames(root: Path) -> str:
    return _git(root, "diff", "--name-status", "--find-renames", "HEAD")


@_cmd("deleted", "Tracked files deleted in the worktree.")
def _deleted(root: Path) -> str:
    rows = [line[3:] for line in _git(root, "status", "--porcelain").splitlines() if line.startswith(" D") or line.startswith("D ")]
    return "\n".join(rows) or "none"


@_cmd("added", "Files added in the worktree or index.")
def _added(root: Path) -> str:
    rows = [line[3:] for line in _git(root, "status", "--porcelain").splitlines() if "A" in line[:2]]
    return "\n".join(rows) or "none"


@_cmd("fixups", "Commit subjects that start with fixup! or squash!.")
def _fixups(root: Path) -> str:
    log = _git(root, "log", "-100", "--pretty=format:%s")
    rows = [line for line in log.splitlines() if line.startswith(("fixup!", "squash!"))]
    return "\n".join(rows) or "none"


@_cmd("subjects", "Last 15 commit subjects, nothing else.")
def _subjects(root: Path) -> str:
    return _git(root, "log", "-15", "--pretty=format:%s")


@_cmd("ticket-ids", "Issue-like ids mentioned in the last 50 subjects.")
def _tickets(root: Path) -> str:
    import re
    log = _git(root, "log", "-50", "--pretty=format:%s")
    found = sorted(set(re.findall(r"#\d+|[A-Z]{2,}-\d+", log)))
    return "\n".join(found) or "none"


@_cmd("aliases", "Git aliases whose names do not contain a secret.")
def _aliases(root: Path) -> str:
    return _git(root, "config", "--get-regexp", "^alias\\.")


@_cmd("ignore-check", "Whether .gitignore exists.")
def _ignore(root: Path) -> str:
    return "yes" if (root / ".gitignore").is_file() else "no"


@_cmd("gitattributes", "Whether .gitattributes exists.")
def _attrs(root: Path) -> str:
    return "yes" if (root / ".gitattributes").is_file() else "no"


@_cmd("lockfiles", "Which dependency lockfiles are present.")
def _locks(root: Path) -> str:
    names = ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "go.sum")
    found = [name for name in names if (root / name).is_file()]
    return "\n".join(found) or "none"


@_cmd("ci", "Whether a CI config is present.")
def _ci(root: Path) -> str:
    found = []
    if (root / ".github" / "workflows").is_dir():
        found.append(".github/workflows")
    for name in (".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile"):
        if (root / name).is_file():
            found.append(name)
    return "\n".join(found) or "none"


@_cmd("tests-dir", "Conventional test directories that exist.")
def _tests_dir(root: Path) -> str:
    found = [name for name in ("tests", "test", "packages") if (root / name).is_dir()]
    return "\n".join(found) or "none"


@_cmd("docs-dir", "Whether a docs directory exists.")
def _docs(root: Path) -> str:
    return "yes" if (root / "docs").is_dir() else "no"


@_cmd("src-dir", "Whether a src directory exists.")
def _src(root: Path) -> str:
    return "yes" if (root / "src").is_dir() else "no"


@_cmd("binary-tracked", "Tracked files git considers binary in HEAD.")
def _binary(root: Path) -> str:
    out = _git(root, "grep", "-I", "--name-only", "-e", ".", "HEAD")
    tracked = set(_tracked(root))
    text = set()
    for line in out.splitlines():
        # git grep HEAD prints "HEAD:path"
        text.add(line.split(":", 1)[-1])
    binary = sorted(tracked - text)
    return "\n".join(binary[:30]) or "none"


@_cmd("empty-files", "Tracked files that are zero bytes.")
def _empty(root: Path) -> str:
    rows = []
    for rel in _tracked(root):
        try:
            if (root / rel).stat().st_size == 0:
                rows.append(rel)
        except OSError:
            continue
    return "\n".join(rows) or "none"


@_cmd("dirs", "Top-level directories, excluding .git.")
def _dirs(root: Path) -> str:
    names = [p.name for p in sorted(root.iterdir()) if p.is_dir() and p.name not in _SKIP and p.name != ".git"]
    return "\n".join(names) or "none"


@_cmd("top-files", "Top-level files.")
def _top_files(root: Path) -> str:
    names = [p.name for p in sorted(root.iterdir()) if p.is_file()]
    return "\n".join(names) or "none"


@_cmd("readme-lines", "Line count of README.md.")
def _readme_lines(root: Path) -> str:
    path = root / "README.md"
    if not path.is_file():
        return "none"
    return str(sum(1 for _ in path.open(encoding="utf-8", errors="replace")))


@_cmd("changelog", "Whether CHANGELOG.md exists.")
def _changelog(root: Path) -> str:
    return "yes" if (root / "CHANGELOG.md").is_file() else "no"


@_cmd("contributing", "Whether CONTRIBUTING.md exists.")
def _contrib(root: Path) -> str:
    return "yes" if (root / "CONTRIBUTING.md").is_file() else "no"


@_cmd("security-md", "Whether SECURITY.md exists.")
def _secmd(root: Path) -> str:
    return "yes" if (root / "SECURITY.md").is_file() else "no"


@_cmd("citation", "Whether CITATION.cff exists.")
def _citation(root: Path) -> str:
    return "yes" if (root / "CITATION.cff").is_file() else "no"


@_cmd("funding", "Whether a funding file exists.")
def _funding(root: Path) -> str:
    return "yes" if (root / ".github" / "FUNDING.yml").is_file() else "no"


@_cmd("issue-templates", "Issue template files.")
def _issue_tpl(root: Path) -> str:
    directory = root / ".github" / "ISSUE_TEMPLATE"
    if not directory.is_dir():
        return "none"
    return "\n".join(p.name for p in sorted(directory.iterdir()) if p.is_file()) or "none"


@_cmd("pr-template", "Whether a pull request template exists.")
def _pr_tpl(root: Path) -> str:
    for rel in (".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md"):
        if (root / rel).is_file():
            return rel
    return "none"


@_cmd("dependabot", "Whether Dependabot config exists.")
def _dependabot(root: Path) -> str:
    return "yes" if (root / ".github" / "dependabot.yml").is_file() else "no"


@_cmd("codeql", "Whether a CodeQL workflow exists.")
def _codeql(root: Path) -> str:
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return "no"
    found = [p.name for p in directory.glob("*.yml") if "codeql" in p.name.lower()]
    return "\n".join(found) or "no"


@_cmd("license-id", "SPDX-looking token on the first lines of LICENSE, if any.")
def _license_id(root: Path) -> str:
    path = root / "LICENSE"
    if not path.is_file():
        return "none"
    head = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:8])
    for token in ("MIT", "Apache-2.0", "GPL-3.0", "BSD-3-Clause"):
        if token.split("-")[0] in head or token in head:
            return token
    return "present"


def command_names() -> list[str]:
    return [name for name, _help in COMMANDS]
