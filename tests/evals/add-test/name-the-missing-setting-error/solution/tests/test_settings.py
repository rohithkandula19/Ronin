import pytest

from hoplite.settings import Settings, SettingsError, load_settings


def test_loads_a_complete_mapping():
    settings = load_settings({"endpoint": "https://queue.invalid", "retries": 3, "timeout": 5.0})
    assert settings == Settings(endpoint="https://queue.invalid", retries=3, timeout=5.0)


def test_timeout_falls_back_to_the_default():
    settings = load_settings({"endpoint": "https://queue.invalid", "retries": 0})
    assert settings.timeout == 30.0


def test_retries_are_coerced_from_a_string():
    settings = load_settings({"endpoint": "https://queue.invalid", "retries": "7"})
    assert settings.retries == 7


def test_settings_are_frozen():
    settings = load_settings({"endpoint": "https://queue.invalid", "retries": 1})
    try:
        settings.retries = 2
    except Exception:
        return
    raise AssertionError("Settings should be immutable")


def test_a_missing_setting_raises_settings_error_naming_the_key():
    with pytest.raises(SettingsError) as caught:
        load_settings({"endpoint": "https://queue.invalid"})
    assert str(caught.value) == "missing required setting: retries"
