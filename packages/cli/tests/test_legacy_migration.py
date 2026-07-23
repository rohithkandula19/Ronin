"""Tests for the one-time .csk → .ronin config-dir migration."""
from __future__ import annotations

import importlib
from pathlib import Path


def _reload_config(monkeypatch, home: Path):
    """Reload the config module with a patched HOME so USER_DIR points into tmp."""
    monkeypatch.setenv("HOME", str(home))
    import ronin_cli.config as cfg
    importlib.reload(cfg)
    return cfg


def test_migrates_project_dir(monkeypatch, tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / ".csk" / "sessions").mkdir(parents=True)
    (proj / ".csk" / "mcp.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(proj)
    cfg = _reload_config(monkeypatch, tmp_path / "home")
    cfg.migrate_legacy_dirs()
    assert (proj / ".ronin" / "mcp.json").is_file()      # data moved
    assert not (proj / ".csk").exists()                   # legacy gone


def test_merges_into_existing_ronin_without_clobbering(monkeypatch, tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    # nested + conflicting layout: a sessions/ dir in both, plus a name clash
    (proj / ".csk" / "sessions").mkdir(parents=True)
    (proj / ".csk" / "sessions" / "old.json").write_text("old-session", encoding="utf-8")
    (proj / ".csk" / "keep.txt").write_text("LEGACY", encoding="utf-8")
    (proj / ".ronin" / "sessions").mkdir(parents=True)
    (proj / ".ronin" / "sessions" / "new.json").write_text("new-session", encoding="utf-8")
    (proj / ".ronin" / "keep.txt").write_text("CURRENT", encoding="utf-8")
    monkeypatch.chdir(proj)
    cfg = _reload_config(monkeypatch, tmp_path / "home")
    cfg.migrate_legacy_dirs()
    # legacy sessions merged in; both old and new sessions now live under .ronin
    assert (proj / ".ronin" / "sessions" / "old.json").is_file()
    assert (proj / ".ronin" / "sessions" / "new.json").is_file()
    # a name clash never overwrites the current file
    assert (proj / ".ronin" / "keep.txt").read_text() == "CURRENT"
    # the legacy dir is gone (clashing keep.txt aside, everything merged)
    assert not (proj / ".csk" / "sessions").exists()


def test_noop_when_no_legacy(monkeypatch, tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    cfg = _reload_config(monkeypatch, tmp_path / "home")
    cfg.migrate_legacy_dirs()  # must not raise or create anything
    assert not (proj / ".ronin").exists()


def test_migrates_legacy_home_config(monkeypatch, tmp_path: Path) -> None:
    """~/.ronin/config.toml (pre-XDG user-global) is carried to ~/.config/ronin
    so an upgrade doesn't strand it and re-trigger the first-run wizard."""
    home = tmp_path / "home"
    (home / ".ronin").mkdir(parents=True)
    (home / ".ronin" / "config.toml").write_text('provider = "groq"\n', encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)  # cwd is NOT home, so ~/.ronin is legacy-global
    cfg = _reload_config(monkeypatch, home)
    cfg.migrate_legacy_dirs()
    moved = home / ".config" / "ronin" / "config.toml"
    assert moved.is_file()
    assert 'provider = "groq"' in moved.read_text()
    assert not (home / ".ronin").exists()  # legacy gone
    # and the config is now discoverable / no longer looks "missing"
    assert cfg.find_config_path() == moved


def test_legacy_home_config_found_before_migration(monkeypatch, tmp_path: Path) -> None:
    """find_config_path resolves ~/.ronin/config.toml directly (so `ronin doctor`
    reports the real path, not 'none') even if migration hasn't run yet."""
    home = tmp_path / "home"
    (home / ".ronin").mkdir(parents=True)
    (home / ".ronin" / "config.toml").write_text('provider = "gemini"\n', encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    cfg = _reload_config(monkeypatch, home)
    assert cfg.find_config_path() == home / ".ronin" / "config.toml"


def test_home_cwd_ronin_not_swept_to_user_dir(monkeypatch, tmp_path: Path) -> None:
    """When ronin runs from $HOME, ./.ronin IS ~/.ronin but is the project-local
    config — it must not be migrated into the user-global ~/.config/ronin."""
    home = tmp_path / "home"
    (home / ".ronin").mkdir(parents=True)
    (home / ".ronin" / "config.toml").write_text('provider = "anthropic"\n', encoding="utf-8")
    monkeypatch.chdir(home)  # cwd == home → .ronin is project-local
    cfg = _reload_config(monkeypatch, home)
    cfg.migrate_legacy_dirs()
    assert (home / ".ronin" / "config.toml").is_file()          # left in place
    assert not (home / ".config" / "ronin" / "config.toml").exists()  # not swept
