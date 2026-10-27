"""Tests for the translation files."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from custom_components.froeling_lambdatronic.climate import PRESETS
from custom_components.froeling_lambdatronic.const import (
    BOILER_STATE_KEYS,
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
    ("path", "keys"),
    [
        ("sensor/system_state/state", SYSTEM_STATE_KEYS.values()),
        ("sensor/boiler_state/state", BOILER_STATE_KEYS.values()),
        ("select/system_operating_mode/state", OPERATING_MODE_KEYS.values()),
        ("climate/heating_circuit/state_attributes/preset_mode/state", PRESETS.values()),
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
