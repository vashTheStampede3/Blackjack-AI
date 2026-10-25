"""Pure application-mode contracts; no GUI, capture, models or profile files."""
import builtins
import importlib
import sys

import pytest

from lib.logic.app_modes import (
    APP_MODE_LABELS,
    APP_MODES,
    INDIVIDUAL,
    MULTIPLAYER,
    normalize_mode,
    zones_for_mode,
)


def test_public_modes_contain_exactly_the_two_official_values():
    assert len(APP_MODES) == 2
    assert APP_MODES == ("individual", "multiplayer")
    assert (INDIVIDUAL, MULTIPLAYER) == APP_MODES


def test_public_mode_labels():
    assert APP_MODE_LABELS == {
        "individual": "Individual",
        "multiplayer": "Multijugador",
    }


@pytest.mark.parametrize("mode,zones", [
    ("individual", ("mine", "dealer")),
    ("multiplayer", ("table", "mine", "dealer")),
])
def test_required_zones(mode, zones):
    assert zones_for_mode(mode) == zones


@pytest.mark.parametrize("mode", ["unknown", "single_seat", "table", "seven_seats", None])
def test_zones_reject_nonpublic_modes(mode):
    with pytest.raises(ValueError, match="Modo desconocido"):
        zones_for_mode(mode)


@pytest.mark.parametrize("mode,expected", [
    ("individual", "individual"),
    ("multiplayer", "multiplayer"),
    ("single_seat", "individual"),
    ("table", "multiplayer"),
])
def test_normalize_supported_modes_and_aliases(mode, expected):
    assert normalize_mode(mode) == expected


def test_seven_seats_requires_explicit_migration():
    with pytest.raises(ValueError, match="histórico seven_seats.*migración explícita"):
        normalize_mode("seven_seats")


@pytest.mark.parametrize("mode", [None, "unknown", "", "Individual", 7, [], {}])
def test_normalize_rejects_invalid_values_without_default(mode):
    with pytest.raises(ValueError, match="Modo desconocido"):
        normalize_mode(mode)


def test_import_does_not_import_other_application_modules(monkeypatch):
    module = sys.modules["lib.logic.app_modes"]
    original_import = builtins.__import__
    before = {name for name in sys.modules if name == "lib" or name.startswith("lib.")}

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name == "lib" or name.startswith("lib.") or name == "tkinter" or name.startswith("tkinter."):
            raise AssertionError(f"Unexpected application dependency: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(module)
    after = {name for name in sys.modules if name == "lib" or name.startswith("lib.")}
    assert after == before
    assert module.APP_MODES == ("individual", "multiplayer")
