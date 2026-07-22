"""Industry Pack SDK: manifest validation, discovery, and fail-closed activation."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_industry_sdk import (
    ActivationError,
    ManifestError,
    PackRegistry,
    PackStatus,
    discover_packs,
    load_manifest,
    parse_manifest,
)

VALID = {
    "id": "healthcare",
    "name": "Healthcare Information",
    "version": "1.0.0",
    "status": "beta",
    "risk_level": "high",
    "description": "Educational healthcare information and record organization",
    "supported_roles": ["patient", "caregiver", "clinician"],
    "supported_countries": ["US"],
    "supported_languages": ["en"],
    "default_adapter": "healthcare-en-us-v1",
    "allowed_model_capabilities": ["text", "vision", "retrieval", "structured_output"],
    "allowed_tools": ["document_parser", "terminology_lookup"],
    "blocked_capabilities": ["autonomous_diagnosis", "prescription_change"],
    "required_policies": ["healthcare-information"],
    "required_eval_suites": ["healthcare-grounding", "healthcare-safety"],
}


def write_pack(root: Path, manifest_yaml: str, name: str) -> Path:
    pack = root / name
    pack.mkdir()
    (pack / "manifest.yaml").write_text(manifest_yaml, encoding="utf-8")
    return pack


# ---------------------------------------------------------------- manifest

def test_valid_manifest_parses():
    m = parse_manifest(VALID)
    assert m.id == "healthcare"
    assert m.status is PackStatus.beta
    assert "autonomous_diagnosis" in m.blocked_capabilities


def test_high_risk_pack_requires_explicit_limits():
    bad = dict(VALID, blocked_capabilities=[], required_policies=[], required_eval_suites=[])
    with pytest.raises(ManifestError, match="high-risk"):
        parse_manifest(bad)


def test_stable_requires_eval_suites():
    bad = dict(VALID, risk_level="low", status="stable", required_eval_suites=[])
    with pytest.raises(ManifestError, match="stable"):
        parse_manifest(bad)


def test_unknown_capability_rejected():
    bad = dict(VALID, allowed_model_capabilities=["text", "telepathy"])
    with pytest.raises(ManifestError, match="telepathy"):
        parse_manifest(bad)


def test_allow_and_block_overlap_rejected():
    bad = dict(VALID, blocked_capabilities=["text"])
    with pytest.raises(ManifestError, match="allows and blocks"):
        parse_manifest(bad)


def test_extra_fields_rejected():
    bad = dict(VALID, surprise_field=1)
    with pytest.raises(ManifestError):
        parse_manifest(bad)


def test_bad_country_and_language_shapes():
    with pytest.raises(ManifestError):
        parse_manifest(dict(VALID, supported_countries=["USA"]))
    with pytest.raises(ManifestError):
        parse_manifest(dict(VALID, supported_languages=["english"]))


def test_load_manifest_missing_file(tmp_path: Path):
    with pytest.raises(ManifestError, match="cannot read"):
        load_manifest(tmp_path / "nope.yaml")


# --------------------------------------------------------------- discovery

def _yaml(d: dict) -> str:
    import yaml

    return yaml.safe_dump(d, sort_keys=False)


def test_discovery_loads_valid_and_names_broken(tmp_path: Path):
    write_pack(tmp_path, _yaml(VALID), "healthcare")
    write_pack(tmp_path, "status: [unclosed", "broken")
    (tmp_path / "no-manifest").mkdir()

    result = discover_packs(tmp_path)
    assert [p.manifest.id for p in result.packs] == ["healthcare"]
    assert "broken" in result.errors
    assert result.errors["no-manifest"] == "missing manifest.yaml"
    assert not result.ok


def test_discovery_rejects_id_dir_mismatch(tmp_path: Path):
    write_pack(tmp_path, _yaml(VALID), "wrong-dir-name")
    result = discover_packs(tmp_path)
    assert result.packs == []
    assert "does not match directory" in result.errors["wrong-dir-name"]


# -------------------------------------------------------------- activation

def make_registry(tmp_path: Path, **overrides) -> PackRegistry:
    manifest = dict(VALID, **overrides)
    write_pack(tmp_path, _yaml(manifest), manifest["id"])
    return PackRegistry.from_discovery(discover_packs(tmp_path))


def test_activation_happy_path(tmp_path: Path):
    reg = make_registry(tmp_path)
    m = reg.activate("healthcare", role="patient", country="US", language="en")
    assert m.id == "healthcare"


def test_disabled_pack_cannot_activate(tmp_path: Path):
    reg = make_registry(tmp_path, status="disabled")
    with pytest.raises(ActivationError, match="disabled"):
        reg.activate("healthcare", role="patient", country="US", language="en")


def test_unsupported_role_country_language_refused(tmp_path: Path):
    reg = make_registry(tmp_path)
    with pytest.raises(ActivationError, match="role"):
        reg.activate("healthcare", role="astronaut", country="US", language="en")
    with pytest.raises(ActivationError, match="country"):
        reg.activate("healthcare", role="patient", country="FR", language="en")
    with pytest.raises(ActivationError, match="language"):
        reg.activate("healthcare", role="patient", country="US", language="fr")


def test_unknown_pack_refused(tmp_path: Path):
    reg = make_registry(tmp_path)
    with pytest.raises(ActivationError, match="unknown"):
        reg.activate("finance", role="analyst", country="US", language="en")


def test_activation_fails_closed_on_unhealthy_pack(tmp_path: Path):
    reg = make_registry(tmp_path)
    with pytest.raises(ActivationError, match="health check"):
        reg.activate(
            "healthcare",
            role="patient",
            country="US",
            language="en",
            known_tools=["document_parser"],  # terminology_lookup missing
            known_eval_suites=["healthcare-grounding", "healthcare-safety"],
            known_adapters=["healthcare-en-us-v1"],
        )


def test_health_check_reports_missing_references(tmp_path: Path):
    reg = make_registry(tmp_path)
    report = reg.health_check(
        "healthcare", known_tools=[], known_eval_suites=[], known_adapters=[]
    )
    assert not report.healthy
    messages = " | ".join(i.message for i in report.issues)
    assert "document_parser" in messages
    assert "healthcare-grounding" in messages
    # missing default adapter is a warning (base model fallback), not an error
    adapter_issues = [i for i in report.issues if "adapter" in i.message]
    assert adapter_issues and all(i.severity == "warning" for i in adapter_issues)


def test_required_policy_without_file_is_unhealthy(tmp_path: Path):
    reg = make_registry(tmp_path)  # VALID requires policy 'healthcare-information'
    report = reg.health_check(
        "healthcare",
        known_tools=["document_parser", "terminology_lookup"],
        known_eval_suites=["healthcare-grounding", "healthcare-safety"],
        known_adapters=["healthcare-en-us-v1"],
    )
    assert not report.healthy
    assert any("healthcare-information" in i.message for i in report.issues)

    pack_dir = tmp_path / "healthcare"
    (pack_dir / "policies").mkdir()
    (pack_dir / "policies" / "healthcare-information.yaml").write_text(
        "id: healthcare-information\n", encoding="utf-8"
    )
    report2 = reg.health_check(
        "healthcare",
        known_tools=["document_parser", "terminology_lookup"],
        known_eval_suites=["healthcare-grounding", "healthcare-safety"],
        known_adapters=["healthcare-en-us-v1"],
    )
    assert report2.healthy


def test_pack_doc_renders_blocked_capabilities(tmp_path: Path):
    reg = make_registry(tmp_path)
    doc = reg.pack_doc("healthcare")
    assert "autonomous_diagnosis" in doc
    assert "Healthcare Information" in doc


def test_list_packs_can_exclude_disabled(tmp_path: Path):
    write_pack(tmp_path, _yaml(VALID), "healthcare")
    write_pack(
        tmp_path,
        _yaml(dict(VALID, id="finance", name="Finance", status="disabled")),
        "finance",
    )
    reg = PackRegistry.from_discovery(discover_packs(tmp_path))
    assert {m.id for m in reg.list_packs()} == {"finance", "healthcare"}
    assert {m.id for m in reg.list_packs(include_disabled=False)} == {"healthcare"}
