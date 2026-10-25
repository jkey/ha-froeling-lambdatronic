"""Tests for the translation files."""

import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.froeling_lambdatronic.const import (
    BOILER_STATE_KEYS,
    HEATING_CIRCUIT_MODE_KEYS,
    OPERATING_MODE_KEYS,
    SYSTEM_STATE_KEYS,
)

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
    ("platform", "translation_key", "keys"),
    [
        ("sensor", "system_state", SYSTEM_STATE_KEYS),
        ("sensor", "boiler_state", BOILER_STATE_KEYS),
        ("select", "heating_circuit_mode", HEATING_CIRCUIT_MODE_KEYS),
        ("select", "system_operating_mode", OPERATING_MODE_KEYS),
    ],
)
def test_state_translations(platform: str, translation_key: str, keys: dict[int, str]) -> None:
    """Every state key of an enum entity has a translation."""
    states = _load("strings.json")["entity"][platform][translation_key]["state"]
    assert set(states) == set(keys.values())
    assert len(set(keys.values())) == len(keys)
