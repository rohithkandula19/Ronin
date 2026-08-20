"""Patch preflight verifies syntax before a coding tool mutates a file."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.patch_verify import PatchDiagnostic, verify_patch
from ronin_cli.code_tools import build_code_tools


def _handlers(root: Path) -> dict:
    return {tool.name: tool.handler for tool in build_code_tools(root)}


def test_python_parse_error_is_rejected_with_location(tmp_path: Path) -> None:
    result = verify_patch(tmp_path / "app.py", "", "def broken(:\n    pass\n")

    assert result.checked is True
    assert result.valid is False
    assert result.diagnostics[0].line == 1
    assert "invalid syntax" in result.summary().lower()


def test_python_public_api_removal_is_honest_warning(tmp_path: Path) -> None:
    before = "def public():\n    return 1\n\ndef _private():\n    return 2\n"
    result = verify_patch(tmp_path / "app.py", before, "def replacement():\n    return 1\n")

    assert result.valid is True
    assert result.removed_public == ("public",)
    assert "public API removed: public" in result.summary()


def test_literal_all_defines_public_api_expectation(tmp_path: Path) -> None:
    before = "__all__ = ['keep', 'remove']\ndef keep(): pass\ndef remove(): pass\n"
    after = "__all__ = ['keep']\ndef keep(): pass\n"

    result = verify_patch(tmp_path / "public.py", before, after)

    assert result.valid is True
    assert result.removed_public == ("remove",)


def test_unknown_file_type_is_explicitly_skipped(tmp_path: Path) -> None:
    result = verify_patch(tmp_path / "notes.md", "before", "after")

    assert result.valid is True
    assert result.checked is False
    assert "not available" in result.summary()


def test_json_and_toml_are_parsed_without_evaluating_a_patch(tmp_path: Path) -> None:
    valid_json = verify_patch(tmp_path / "package.json", "", '{"name": "ronin"}')
    invalid_json = verify_patch(tmp_path / "package.json", "", "{bad")
    valid_toml = verify_patch(tmp_path / "pyproject.toml", "", "[project]\nname = 'ronin'\n")
    invalid_toml = verify_patch(tmp_path / "pyproject.toml", "", "[project\nname = 'ronin'\n")

    assert valid_json.checked and valid_json.valid
    assert invalid_json.checked and not invalid_json.valid
    assert valid_toml.checked and valid_toml.valid
    assert invalid_toml.checked and not invalid_toml.valid


def test_typescript_uses_injected_parser_without_evaluating_source(tmp_path: Path) -> None:
    seen: list[str] = []

    def parser(source: str, path: Path, root: Path | None):
        seen.append(source)
        assert path.name == "view.ts"
        assert root == tmp_path
        return ()

    result = verify_patch(
        tmp_path / "view.ts", "", "export const answer: number = 42;", root=tmp_path,
        typescript_parser=parser,
    )

    assert result.checked is True
    assert result.valid is True
    assert seen == ["export const answer: number = 42;"]


def test_typescript_diagnostic_blocks_patch(tmp_path: Path) -> None:
    result = verify_patch(
        tmp_path / "view.ts", "", "const = ;", root=tmp_path,
        typescript_parser=lambda *_: (PatchDiagnostic("Expression expected", 1, 7),),
    )

    assert result.checked is True
    assert result.valid is False
    assert "Expression expected at line 1, column 7" in result.summary()


def test_invalid_python_write_does_not_create_file(tmp_path: Path) -> None:
    write = _handlers(tmp_path)["write_file"]

    message = write(path="broken.py", content="def broken(:\n    pass\n")

    assert message.startswith("ERROR: patch verification failed")
    assert not (tmp_path / "broken.py").exists()


def test_invalid_python_edit_leaves_existing_file_unchanged(tmp_path: Path) -> None:
    source = "def answer():\n    return 42\n"
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    edit = _handlers(tmp_path)["edit_file"]

    message = edit(path="app.py", old_string="return 42", new_string="return (")

    assert message.startswith("ERROR: patch verification failed")
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == source


def test_multi_edit_is_all_or_nothing_when_preflight_fails(tmp_path: Path) -> None:
    source = "def first():\n    return 1\n\ndef second():\n    return 2\n"
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    multi_edit = _handlers(tmp_path)["multi_edit"]

    message = multi_edit(path="app.py", edits=[
        {"old_string": "return 1", "new_string": "return 10"},
        {"old_string": "return 2", "new_string": "return ("},
    ])

    assert message.startswith("ERROR: patch verification failed")
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == source


def test_public_api_removal_is_reported_after_successful_edit(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    edit = _handlers(tmp_path)["edit_file"]

    message = edit(path="app.py", old_string="def public", new_string="def _public")

    assert "python syntax verified; public API removed: public" in message
    assert "def _public" in (tmp_path / "app.py").read_text(encoding="utf-8")
