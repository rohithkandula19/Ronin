"""Parse proposed source before a coding tool writes it to disk.

This is a deliberately small preflight, not a replacement for a repository's
test suite. It catches syntax damage at the edit boundary, where the agent can
still revise without leaving a broken file behind, and reports Python public API
removals as an honest warning rather than silently calling a patch safe.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class PatchDiagnostic:
    message: str
    line: int | None = None
    column: int | None = None

    def render(self) -> str:
        location = ""
        if self.line is not None:
            location = f" at line {self.line}"
            if self.column is not None:
                location += f", column {self.column}"
        return self.message + location


@dataclass(frozen=True)
class PatchVerification:
    language: str | None
    checked: bool
    valid: bool
    diagnostics: tuple[PatchDiagnostic, ...] = ()
    removed_public: tuple[str, ...] = ()
    skip_reason: str | None = None

    def summary(self) -> str:
        if not self.checked:
            return self.skip_reason or "syntax verification skipped"
        if not self.valid:
            detail = self.diagnostics[0].render() if self.diagnostics else "parse failed"
            return f"{self.language} syntax error: {detail}"
        text = f"{self.language} syntax verified"
        if self.removed_public:
            text += "; public API removed: " + ", ".join(self.removed_public)
        return text


TypescriptParser = Callable[[str, Path, Path | None], tuple[PatchDiagnostic, ...] | None]


def verify_patch(
    path: Path | str,
    before: str,
    after: str,
    *,
    root: Path | str | None = None,
    typescript_parser: TypescriptParser | None = None,
) -> PatchVerification:
    """Verify a proposed replacement without touching the file system.

    Python uses :func:`ast.parse`. For TypeScript/TSX, Ronin asks a locally
    installed ``typescript`` package for parse diagnostics, never downloading a
    compiler or evaluating the proposed code. Other languages remain unchanged
    and report an explicit skipped state rather than a false claim of validation.
    """
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix in {".py", ".pyi"}:
        return _verify_python(before, after)
    if suffix in {".ts", ".tsx", ".mts", ".cts"}:
        parser = typescript_parser or _typescript_diagnostics
        try:
            diagnostics = parser(after, target, Path(root) if root is not None else None)
        except Exception:  # noqa: BLE001 - unavailable parsers must not break edits
            diagnostics = None
        if diagnostics is None:
            return PatchVerification(
                language="typescript",
                checked=False,
                valid=True,
                skip_reason="TypeScript parser unavailable; syntax not checked",
            )
        return PatchVerification(
            language="typescript",
            checked=True,
            valid=not diagnostics,
            diagnostics=diagnostics,
        )
    return PatchVerification(
        language=None,
        checked=False,
        valid=True,
        skip_reason="syntax verification not available for this file type",
    )


def _verify_python(before: str, after: str) -> PatchVerification:
    try:
        after_tree = ast.parse(after)
    except (SyntaxError, TypeError, ValueError) as exc:
        return PatchVerification(
            language="python",
            checked=True,
            valid=False,
            diagnostics=(PatchDiagnostic(
                message=getattr(exc, "msg", str(exc)) or "invalid Python",
                line=getattr(exc, "lineno", None),
                column=getattr(exc, "offset", None),
            ),),
        )

    try:
        before_tree = ast.parse(before)
    except (SyntaxError, TypeError, ValueError):
        before_tree = None
    removed = () if before_tree is None else tuple(sorted(
        _public_symbols(before_tree) - _public_symbols(after_tree)
    ))
    return PatchVerification(
        language="python",
        checked=True,
        valid=True,
        removed_public=removed,
    )


def _public_symbols(tree: ast.Module) -> set[str]:
    """Top-level public definitions, respecting a literal ``__all__`` if present."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            break
        if isinstance(value, (list, tuple)) and all(isinstance(name, str) for name in value):
            return set(value)
        break
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    }


_TYPESCRIPT_PARSE = r"""
const fs = require('fs');
const path = require('path');
const project = path.resolve(process.cwd()) + path.sep;
const typescriptPath = path.resolve(require.resolve('typescript'));
if (!typescriptPath.startsWith(project)) process.exit(2);
const ts = require(typescriptPath);
const file = process.argv[1];
const text = fs.readFileSync(0, 'utf8');
const kind = /\.tsx$/i.test(file) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
const diagnostics = source.parseDiagnostics.map((item) => {
  const pos = source.getLineAndCharacterOfPosition(item.start || 0);
  return {
    message: ts.flattenDiagnosticMessageText(item.messageText, ' '),
    line: pos.line + 1,
    column: pos.character + 1,
  };
});
process.stdout.write(JSON.stringify(diagnostics));
"""


def _typescript_diagnostics(
    source: str, path: Path, root: Path | None,
) -> tuple[PatchDiagnostic, ...] | None:
    """Return TypeScript parse diagnostics, or ``None`` without a local parser."""
    node = shutil.which("node")
    if node is None:
        return None
    try:
        proc = subprocess.run(
            [node, "-e", _TYPESCRIPT_PARSE, str(path)],
            input=source,
            text=True,
            cwd=root,
            capture_output=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        raw = json.loads(proc.stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, list):
        return None
    diagnostics: list[PatchDiagnostic] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("message"), str):
            return None
        line = item.get("line")
        column = item.get("column")
        diagnostics.append(PatchDiagnostic(
            message=item["message"],
            line=line if isinstance(line, int) else None,
            column=column if isinstance(column, int) else None,
        ))
    return tuple(diagnostics)
