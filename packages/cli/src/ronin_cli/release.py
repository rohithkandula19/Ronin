"""Release versioning, validation, and artifact checks for Ronin packages."""
from __future__ import annotations

import argparse
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


_PEP440_VERSION = r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?"
_SEMVER_RE = re.compile(rf"^(\d+)\.(\d+)\.(\d+)(?:(?:a|b|rc)\d+)?$")
_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)(?:-(a|b|rc)\.(\d+))?$")
_VERSION_PATTERNS = [
    re.compile(rf'(__version__\s*=\s*["\'])({_PEP440_VERSION})(["\'])'),
    re.compile(rf'(^version\s*=\s*["\'])({_PEP440_VERSION})(["\'])', re.MULTILINE),
]

# These are the distributions built, checked, and published by release.yml.
RELEASE_PACKAGE_DIRS = (
    "packages/agent-patterns",
    "packages/eval-suite",
    "packages/memory",
    "packages/hardening",
    "packages/mcp-servers",
    "packages/cli",
    "packages/relay",
)
RELEASE_VERSION_PATHS = (
    "pyproject.toml",
    *(f"{directory}/pyproject.toml" for directory in RELEASE_PACKAGE_DIRS),
    "packages/cli/src/ronin_cli/__init__.py",
    "packages/relay/src/ronin_relay/__init__.py",
)
CLI_INTERNAL_DISTRIBUTIONS = (
    "ronin-agent-patterns",
    "ronin-hardening",
    "ronin-memory",
    "ronin-mcp-servers",
    "ronin-eval-suite",
)


@dataclass(frozen=True)
class ReleasePackage:
    """A publishable package and its path relative to the repository root."""

    name: str
    directory: str


def version_from_tag(tag: str) -> str:
    """Convert a release tag (``v1.2.3-rc.1``) to its PEP 440 version."""
    match = _TAG_RE.fullmatch(tag.strip())
    if not match:
        raise ValueError(f"invalid release tag: {tag!r}")
    base, phase, number = match.groups()
    return f"{base}{phase or ''}{number or ''}"


def normalize_release_version(version: str) -> str:
    """Normalize a PEP 440 release version or its friendly tag form."""
    value = version.strip()
    if value.startswith("v"):
        return version_from_tag(value)
    if not re.fullmatch(_PEP440_VERSION, value):
        raise ValueError(f"not a supported release version: {version!r}")
    return value


def bump_version(version: str, kind: str = "patch") -> str:
    """Return ``version`` bumped by ``kind`` (major|minor|patch). Pure."""
    match = _SEMVER_RE.fullmatch(version.strip())
    if not match:
        raise ValueError(f"not a semver: {version!r}")
    major, minor, patch = (int(value) for value in match.groups())
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    elif kind == "patch":
        patch += 1
    else:
        raise ValueError(f"unknown bump kind: {kind!r}")
    return f"{major}.{minor}.{patch}"


def set_version_in_text(text: str, new: str) -> tuple[str, int]:
    """Rewrite ``__version__`` and project ``version`` declarations. Pure."""
    replacement_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal replacement_count
        replacement_count += 1
        return f"{match.group(1)}{new}{match.group(3)}"

    for pattern in _VERSION_PATTERNS:
        text = pattern.sub(replace, text)
    return text, replacement_count


def find_version_files(root: Path | str) -> list[Path]:
    """Find all generic ``__version__`` and root ``pyproject.toml`` declarations.

    This remains available for callers that intentionally inspect an arbitrary
    project. Ronin releases use :func:`release_version_files` instead.
    """
    repo = Path(root).resolve()
    files: list[Path] = []
    for path in repo.rglob("__init__.py"):
        if any(part in {".venv", "venv", "node_modules", "__pycache__", ".git"} for part in path.parts):
            continue
        try:
            if "__version__" in path.read_text(encoding="utf-8", errors="ignore"):
                files.append(path)
        except OSError:
            continue
    project_file = repo / "pyproject.toml"
    if project_file.is_file():
        files.append(project_file)
    return files


def current_version(files: list[Path]) -> str | None:
    """Return the first version found across ``files``."""
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in _VERSION_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(2)
    return None


def release_version_files(root: Path | str) -> tuple[Path, ...]:
    """Return every Ronin version source that must move together."""
    repo = Path(root).resolve()
    return tuple(repo / relative for relative in RELEASE_VERSION_PATHS)


def release_packages(root: Path | str) -> tuple[ReleasePackage, ...]:
    """Read the publishable package names from the fixed release manifest."""
    repo = Path(root).resolve()
    packages: list[ReleasePackage] = []
    for directory in RELEASE_PACKAGE_DIRS:
        project_file = repo / directory / "pyproject.toml"
        try:
            raw = tomllib.loads(project_file.read_text(encoding="utf-8"))
            name = raw["project"]["name"]
        except (KeyError, OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"could not read release package metadata: {project_file}") from exc
        if not isinstance(name, str) or not name:
            raise ValueError(f"release package has no project name: {project_file}")
        packages.append(ReleasePackage(name=name, directory=directory))
    return tuple(packages)


def _project_metadata(path: Path) -> dict:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        project = raw["project"]
    except (KeyError, OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"could not read project metadata: {path}") from exc
    if not isinstance(project, dict):
        raise ValueError(f"project metadata must be an object: {path}")
    return project


def release_validation_errors(root: Path | str, tag: str) -> list[str]:
    """Return release consistency errors for ``tag`` without changing files."""
    repo = Path(root).resolve()
    try:
        expected = version_from_tag(tag)
    except ValueError as exc:
        return [str(exc)]

    errors: list[str] = []
    for path in release_version_files(repo):
        if not path.is_file():
            errors.append(f"missing release version file: {path.relative_to(repo)}")
            continue
        actual = current_version([path])
        if actual != expected:
            errors.append(f"{path.relative_to(repo)} declares {actual or 'no version'}, expected {expected}")

    cli_file = repo / "packages/cli/pyproject.toml"
    try:
        dependencies = _project_metadata(cli_file).get("dependencies", [])
    except ValueError as exc:
        errors.append(str(exc))
        dependencies = []
    if not isinstance(dependencies, list):
        errors.append("packages/cli/pyproject.toml dependencies must be a list")
        dependencies = []
    dependency_set = {dependency for dependency in dependencies if isinstance(dependency, str)}
    for distribution in CLI_INTERNAL_DISTRIBUTIONS:
        expected_pin = f"{distribution}=={expected}"
        if expected_pin not in dependency_set:
            errors.append(f"packages/cli/pyproject.toml must pin {expected_pin}")
    return errors


def validate_release(root: Path | str, tag: str) -> None:
    """Raise ``ValueError`` when a release tag and source tree disagree."""
    errors = release_validation_errors(root, tag)
    if errors:
        raise ValueError("release validation failed:\n- " + "\n- ".join(errors))


def set_internal_dependency_versions(text: str, version: str) -> tuple[str, int]:
    """Rewrite the CLI's exact internal-distribution pins. Pure."""
    replacement_count = 0
    for distribution in CLI_INTERNAL_DISTRIBUTIONS:
        pattern = re.compile(rf'({re.escape(distribution)}==){_PEP440_VERSION}')
        text, count = pattern.subn(rf"\g<1>{version}", text)
        replacement_count += count
    return text, replacement_count


def prepare_release(root: Path | str, version: str) -> tuple[Path, ...]:
    """Synchronize Ronin release versions and CLI dependency pins atomically enough.

    All replacement targets are checked before files are written, which prevents a
    partially prepared release when the repository layout has drifted.
    """
    repo = Path(root).resolve()
    target = normalize_release_version(version)
    files = release_version_files(repo)
    originals: dict[Path, str] = {}
    updates: dict[Path, str] = {}

    for path in files:
        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"could not read release version file: {path}") from exc
        updated, replacements = set_version_in_text(original, target)
        if replacements != 1:
            raise ValueError(f"expected one version declaration in {path}, found {replacements}")
        originals[path] = original
        updates[path] = updated

    cli_file = repo / "packages/cli/pyproject.toml"
    updated, replacements = set_internal_dependency_versions(updates[cli_file], target)
    if replacements != len(CLI_INTERNAL_DISTRIBUTIONS):
        raise ValueError(
            f"expected {len(CLI_INTERNAL_DISTRIBUTIONS)} internal dependency pins in {cli_file}, found {replacements}"
        )
    updates[cli_file] = updated

    changed: list[Path] = []
    for path in files:
        if updates[path] != originals[path]:
            path.write_text(updates[path], encoding="utf-8")
            changed.append(path)
    return tuple(changed)


def release_artifact_errors(root: Path | str, tag: str, artifact_dir: Path | str) -> list[str]:
    """Return missing sdist/wheel errors for the fixed release manifest."""
    repo = Path(root).resolve()
    artifacts = Path(artifact_dir)
    try:
        version = version_from_tag(tag)
        packages = release_packages(repo)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    for package in packages:
        normalized = package.name.replace("-", "_")
        prefix = f"{normalized}-{version}"
        if not list(artifacts.glob(f"{prefix}-*.whl")):
            errors.append(f"missing wheel for {package.name} {version}")
        if not (artifacts / f"{prefix}.tar.gz").is_file():
            errors.append(f"missing sdist for {package.name} {version}")
    return errors


def validate_release_artifacts(root: Path | str, tag: str, artifact_dir: Path | str) -> None:
    """Raise ``ValueError`` when the release artifact set is incomplete."""
    errors = release_artifact_errors(root, tag, artifact_dir)
    if errors:
        raise ValueError("release artifact validation failed:\n- " + "\n- ".join(errors))


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or prepare a Ronin release.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--tag", help="Release tag, for example v1.2.3-rc.1.")
    parser.add_argument("--prepare", help="Version to write before validation.")
    parser.add_argument("--artifact-dir", help="Validate release artifacts in this directory.")
    parser.add_argument("--print-package-dirs", action="store_true", help="Print package directories for a release build.")
    args = parser.parse_args(argv)

    try:
        if args.prepare:
            prepare_release(args.root, args.prepare)
        if args.tag:
            validate_release(args.root, args.tag)
        if args.artifact_dir:
            if not args.tag:
                parser.error("--artifact-dir requires --tag")
            validate_release_artifacts(args.root, args.tag, args.artifact_dir)
        if args.print_package_dirs:
            for package in release_packages(args.root):
                print(package.directory)
    except ValueError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CI workflow
    raise SystemExit(_main())
