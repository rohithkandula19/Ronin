from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.briefing import compute_briefing_data
from ronin_cli.briefing_template import (
    BriefingTemplate,
    SECTIONS,
    render_with_template,
)
from ronin_cli.config import RoninConfig
from ronin_cli.main import app
from ronin_cli.tools import build_tools


runner = CliRunner()


def _demo_data():
    return compute_briefing_data(build_tools(RoninConfig(demo_mode=True)))


def test_default_template_has_all_four_sections() -> None:
    t = BriefingTemplate.default()
    assert t.sections == ["revenue", "payments", "engineering", "actions"]
    assert "{{date}}" in t.title


def test_load_missing_file_returns_default(tmp_path: Path) -> None:
    t = BriefingTemplate.load(tmp_path / "absent.toml")
    assert t.sections == ["revenue", "payments", "engineering", "actions"]


def test_load_custom_template(tmp_path: Path) -> None:
    p = tmp_path / "tpl.toml"
    p.write_text(
        'title = "Monday brief — {{date}}"\n'
        'sections = ["revenue", "engineering"]\n',
        encoding="utf-8",
    )
    t = BriefingTemplate.load(p)
    assert t.title.startswith("Monday brief")
    assert t.sections == ["revenue", "engineering"]


def test_render_respects_section_order_and_skips_unknown() -> None:
    data = _demo_data()
    t = BriefingTemplate(
        title="Custom — {{date}}",
        sections=["engineering", "no-such-section", "revenue"],
    )
    md = render_with_template(data, t)
    eng_pos = md.find("🛠 Engineering")
    rev_pos = md.find("💰 Revenue")
    assert eng_pos != -1 and rev_pos != -1
    assert eng_pos < rev_pos, "Engineering should render before Revenue"
    # Payments section was not in the template → not in the output
    assert "💳 Payments" not in md


def test_title_substitution() -> None:
    data = _demo_data()
    t = BriefingTemplate(title="Weekly digest for {{date}}", sections=["revenue"])
    md = render_with_template(data, t)
    assert "Weekly digest for" in md
    assert data.today_iso in md


def test_optional_sections_customers_and_charges() -> None:
    """Customers + Charges aren't in the default template but should render when asked."""
    data = _demo_data()
    t = BriefingTemplate(sections=["customers", "charges"])
    md = render_with_template(data, t)
    assert "👥 Active customers" in md
    assert "🧾 Charges" in md


def test_all_section_ids_have_renderers() -> None:
    for sid in ["revenue", "payments", "engineering", "actions", "customers", "charges"]:
        assert sid in SECTIONS, f"missing renderer for {sid}"


def test_cli_uses_custom_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    tpl = tmp_path / "tpl.toml"
    tpl.write_text(
        'title = "Custom — {{date}}"\n'
        'sections = ["revenue"]\n',
        encoding="utf-8",
    )
    r = runner.invoke(app, ["util", "briefing", "--template", str(tpl), "--raw"])
    assert r.exit_code == 0
    assert "Custom —" in r.stdout
    assert "💰 Revenue" in r.stdout
    # Other sections shouldn't appear when template only lists revenue
    assert "💳 Payments" not in r.stdout
    assert "🛠 Engineering" not in r.stdout


def test_cli_auto_loads_project_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If .ronin/briefing-template.toml exists, the CLI uses it without --template."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    (tmp_path / ".ronin" / "briefing-template.toml").write_text(
        'title = "Auto-loaded — {{date}}"\nsections = ["revenue"]\n',
        encoding="utf-8",
    )
    r = runner.invoke(app, ["util", "briefing", "--raw"])
    assert r.exit_code == 0
    assert "Auto-loaded —" in r.stdout
    assert "💳 Payments" not in r.stdout
