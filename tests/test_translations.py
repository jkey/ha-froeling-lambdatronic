"""Tests for the translation files."""

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.froeling_lambdatronic.climate import PRESETS
from custom_components.froeling_lambdatronic.const import (
    BOILER_STATE_KEYS,
    DOMAIN,
    HOT_WATER_MODE_KEYS,
    ISSUE_PROTOCOL_DISABLED,
    ISSUE_REMOTE_CONTROL_DISABLED,
    OPERATING_MODE_KEYS,
    ConnectionType,
)
from custom_components.froeling_lambdatronic.entity import DEVICE_TRANSLATION_KEYS

from . import setup_integration
from .const import ENTRY_OPTIONS, entry_data

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "froeling_lambdatronic"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((INTEGRATION / name).read_text(encoding="utf-8"))
    return data


def _keys(data: object, prefix: str = "") -> set[str]:
    if not isinstance(data, dict):
        return {prefix}
    return {key for name, value in data.items() for key in _keys(value, f"{prefix}/{name}")}


def test_english_is_strings() -> None:
    """translations/en.json is an exact copy of strings.json."""
    assert _load("translations/en.json") == _load("strings.json")


def test_german_has_same_keys() -> None:
    """translations/de.json translates every string, nothing more."""
    assert _keys(_load("translations/de.json")) == _keys(_load("strings.json"))


@pytest.mark.parametrize(
    ("path", "keys"),
    [
        ("sensor/boiler_state/state", BOILER_STATE_KEYS.values()),
        ("select/system_operating_mode/state", OPERATING_MODE_KEYS.values()),
        ("climate/heating_circuit/state_attributes/preset_mode/state", PRESETS.values()),
        ("water_heater/hot_water/state", HOT_WATER_MODE_KEYS.values()),
    ],
)
def test_state_translations(path: str, keys: Iterable[str]) -> None:
    """Every state of an enum entity or preset has a translation."""
    states: Any = _load("strings.json")["entity"]
    for part in path.split("/"):
        states = states[part]
    keys = list(keys)
    assert set(states) == set(keys)
    assert len(set(keys)) == len(keys)


def _source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(INTEGRATION.glob("*.py")))


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "mock_client", "mock_cloud")
async def test_entity_translations_complete(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    """Every entity of every connection type has its translations; no translation is unused."""
    entities = _load("strings.json")["entity"]
    used: set[tuple[str, str]] = set()
    for connection in ConnectionType:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=2,
            title="Fröling",
            unique_id=f"translations-{connection}",
            data=entry_data(connection),
            options=ENTRY_OPTIONS,
        )
        await setup_integration(hass, entry)
        for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
            key = registry_entry.translation_key
            assert key is not None, registry_entry.entity_id
            assert key in entities[registry_entry.domain], registry_entry.entity_id
            if registry_entry.original_name is not None:
                assert "name" in entities[registry_entry.domain][key], registry_entry.entity_id
            used.add((registry_entry.domain, key))

    assert {(platform, key) for platform, keys in entities.items() for key in keys} == used


def test_exception_and_issue_translations() -> None:
    """Exceptions raised in the code and repair issues are translated; nothing unused is left."""
    strings = _load("strings.json")
    # Translation keys are passed as `translation_key=` or as the `failed_key` of the cloud error handler.
    literals = set(re.findall(r'(?:translation_key|failed_key)(?:: str)? ?= ?"(\w+)"', _source()))
    entity_keys = {key for keys in strings["entity"].values() for key in keys}
    exceptions = set(strings["exceptions"])
    assert literals - entity_keys == exceptions
    assert set(strings["issues"]) == {ISSUE_PROTOCOL_DISABLED, ISSUE_REMOTE_CONTROL_DISABLED}


def test_config_flow_errors_used() -> None:
    """Every config flow error and abort reason appears in the config flow."""
    strings = _load("strings.json")["config"]
    source = (INTEGRATION / "config_flow.py").read_text(encoding="utf-8")
    for key in [*strings["error"], *strings["abort"]]:
        assert f'"{key}"' in source or key.endswith("_successful"), key


def test_device_translations() -> None:
    """Every child device kind has a translated name with the number placeholder where numbered."""
    devices = _load("strings.json")["device"]
    assert set(devices) == set(DEVICE_TRANSLATION_KEYS.values())
    for key, device in devices.items():
        assert ("{number}" in device["name"]) is (key != "circulation_pump"), key


def test_icons_and_services() -> None:
    """Icons only for translated entities; actions described, translated and with an icon."""
    strings = _load("strings.json")
    icons = _load("icons.json")
    for platform, keys in icons["entity"].items():
        assert set(keys) <= set(strings["entity"][platform]), platform
    services = load_yaml_dict(INTEGRATION / "services.yaml")
    assert set(services) == set(strings["services"]) == set(icons["services"])
    for name, service in services.items():
        assert set(service.get("fields", {})) == set(strings["services"][name].get("fields", {})), name
