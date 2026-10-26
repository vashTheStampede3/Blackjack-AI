"""Overwrite protection and three-way editor decisions with isolated storage."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.common import constants
from lib.logic import region_profiles as profiles
from lib.logic.card_layout import CardLayout
from lib.logic.app_modes import zones_for_mode, APP_MODE_LABELS
from lib.interfaces import region_editor as editor_module
from lib.interfaces.region_editor import RegionEditor


RES = (320, 180)
KEY = "320x180"


def payload(mode="individual", resolution=RES, changed=False):
    data = {"mode": mode, "resolution": list(resolution),
            "mine": [[21 if changed else 20, 100], [100, 100], [100, 160], [20, 160]],
            "dealer": [[100, 20], [200, 20], [200, 60], [100, 60]]}
    if mode in ("multiplayer", "table", "single_seat"):
        data["table"] = [[5, 85], [315, 85], [315, 175], [5, 175]]
    return data


def layout(mode="individual", resolution=RES, changed=False):
    return CardLayout.from_payload(payload(mode, resolution, changed), resolution)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    return profiles.store_path()


def read():
    return json.loads(profiles.store_path().read_text(encoding="utf-8"))


def seed(modes):
    data = profiles._empty()
    data["profiles"]["Target"] = profiles._new_profile()
    data["profiles"]["Target"]["layouts"][KEY] = modes
    profiles.store_path().write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("name,exists", [("Target", True), (" Target ", True),
                                        ("target", False), ("Missing", False), ("", False), (" ", False), (None, False)])
def test_profile_name_query_is_read_only(isolated_store, name, exists):
    seed({})
    before = isolated_store.read_bytes()
    assert profiles.profile_exists(name) == exists
    assert isolated_store.read_bytes() == before
    assert profiles.active_name() == "Default"


def test_existence_queries_do_not_create_store_or_migrate_legacy(isolated_store):
    legacy = isolated_store.parent / "regions_320x180.json"
    legacy.write_text('{}', encoding="utf-8")
    assert not profiles.profile_exists("Default")
    assert not profiles.layout_target_exists(RES, "individual", "Default")
    assert not isolated_store.exists() and legacy.exists()


@pytest.mark.parametrize("mode,stored", [("individual", "individual"), ("multiplayer", "multiplayer"),
                                        ("individual", "single_seat"), ("multiplayer", "table")])
@pytest.mark.parametrize("valid", [True, False])
def test_layout_target_counts_invalid_and_legacy_entries(isolated_store, mode, stored, valid):
    seed({stored: payload(stored) if valid else None})
    before = isolated_store.read_bytes()
    assert profiles.layout_target_exists(RES, mode, " Target ")
    assert not profiles.layout_target_exists((640, 360), mode, "Target")
    assert isolated_store.read_bytes() == before
    with pytest.raises(profiles.ProfileConflictError):
        profiles.set_card_layout(layout(mode), profile="Target")
    assert isolated_store.read_bytes() == before


def test_seven_seats_is_not_equivalent():
    seed({"seven_seats": {}, "selected_mode": "seven_seats"})
    assert not profiles.layout_target_exists(RES, "individual", "Target")
    profiles.set_card_layout(layout(), profile="Target")
    assert "seven_seats" in read()["profiles"]["Target"]["layouts"][KEY]


@pytest.mark.parametrize("alias,mode", [("single_seat", "individual"), ("table", "multiplayer")])
def test_authorized_alias_replacement_cleans_only_its_target(alias, mode, isolated_store):
    other = "table" if alias == "single_seat" else "single_seat"
    seed({alias: payload(alias), other: payload(other)})
    before = isolated_store.read_bytes()
    with pytest.raises(profiles.ProfileConflictError):
        profiles.set_card_layout(layout(mode), profile="Target")
    assert isolated_store.read_bytes() == before
    profiles.set_card_layout(layout(mode), profile="Target", overwrite=True)
    modes = read()["profiles"]["Target"]["layouts"][KEY]
    assert alias not in modes and other in modes and mode in modes


@pytest.mark.parametrize("query", ["profile", "layout"])
def test_corrupt_store_query_never_quarantines_or_rewrites(query, isolated_store):
    isolated_store.write_text('{broken', encoding="utf-8")
    before = isolated_store.read_bytes()
    with pytest.raises(ValueError, match="corrupt"):
        if query == "profile":
            profiles.profile_exists("Target")
        else:
            profiles.layout_target_exists(RES, "individual", "Target")
    assert isolated_store.read_bytes() == before
    assert not isolated_store.with_name(isolated_store.name + ".corrupt").exists()


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_new_target_additions_conflict_and_authorized_preservation(isolated_store, mode):
    other_mode = "multiplayer" if mode == "individual" else "individual"
    profiles.set_card_layout(layout(mode), profile="Target")
    profiles.set_card_layout(layout(other_mode), profile="Target")
    profiles.set_card_layout(layout(mode, (640, 360)), profile="Target")
    data = read()
    prof = data["profiles"]["Target"]
    for kind in ("ocr", "controls", "anchors", "source_contexts", "regions"):
        prof[kind][KEY] = {"keep": kind}
    data["profiles"]["Other"] = deepcopy(prof)
    data["active"] = "Other"
    isolated_store.write_text(json.dumps(data), encoding="utf-8")
    before = isolated_store.read_bytes()
    for _ in range(2):
        with pytest.raises(profiles.ProfileConflictError):
            profiles.set_card_layout(layout(mode, changed=True), profile="Target")
        assert isolated_store.read_bytes() == before
    profiles.set_card_layout(layout(mode, changed=True), profile="Target", overwrite=True)
    saved = read()
    result = saved["profiles"]["Target"]
    assert saved["active"] == "Target"
    assert result["layouts"][KEY][mode] == layout(mode, changed=True).to_payload()
    assert result["layouts"][KEY][other_mode] == prof["layouts"][KEY][other_mode]
    assert result["layouts"]["640x360"] == prof["layouts"]["640x360"]
    for kind in ("ocr", "controls", "anchors", "source_contexts", "regions"):
        assert result[kind] == prof[kind]
    assert saved["profiles"]["Other"] == data["profiles"]["Other"]


def test_conflict_check_happens_after_locked_latest_read(monkeypatch, isolated_store):
    seed({})
    original_load = profiles._load

    def concurrent_state():
        assert profiles._LOCK._is_owned()
        data = original_load()
        data["profiles"]["Target"]["layouts"][KEY]["individual"] = payload()
        return data

    monkeypatch.setattr(profiles, "_load", concurrent_state)
    writer = Mock()
    monkeypatch.setattr(profiles, "_write", writer)
    with pytest.raises(profiles.ProfileConflictError):
        profiles.set_card_layout(layout(), profile="Target")
    writer.assert_not_called()


def test_invalid_layout_rejected_before_read(monkeypatch):
    loader = Mock(side_effect=AssertionError("invalid data must not reach storage"))
    monkeypatch.setattr(profiles, "_load", loader)
    with pytest.raises(ValueError):
        profiles.set_card_layout(CardLayout("individual", RES, None, (), ()))
    loader.assert_not_called()


@pytest.mark.parametrize("query", ["profile", "layout"])
def test_access_failure_is_not_absence(monkeypatch, isolated_store, query):
    seed({})
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path == isolated_store:
            raise PermissionError("locked")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(OSError):
        if query == "profile":
            profiles.profile_exists("Target")
        else:
            profiles.layout_target_exists(RES, "individual", "Target")


def fake_editor(mode="individual", name="Target"):
    data = payload(mode)
    return SimpleNamespace(mode=mode, resolution=RES, zone_keys=zones_for_mode(mode),
        zones=[deepcopy(data[k]) for k in zones_for_mode(mode)], scale=1,
        capture=SimpleNamespace(calibration_context={"kind": "video_frame", "frame_index": 5}),
        profile_var=SimpleNamespace(get=lambda: name), profile_entry=Mock(),
        on_save=Mock(), destroy=Mock(), _log_saved=Mock(), _log_save_error=Mock())


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("target", ["new", "existing_profile", "occupied"])
@pytest.mark.parametrize("answer", [True, False, None])
def test_editor_three_way_decisions(monkeypatch, isolated_store, mode, target, answer):
    if target == "existing_profile":
        seed({})
    elif target == "occupied":
        seed({mode: payload(mode)})
    e = fake_editor(mode)
    before = isolated_store.read_bytes() if isolated_store.exists() else None
    points = deepcopy(e.zones)
    dialog, error = Mock(return_value=answer), Mock()
    monkeypatch.setattr(editor_module.messagebox, "askyesnocancel", dialog)
    monkeypatch.setattr(editor_module.messagebox, "showerror", error)
    original = profiles.set_card_layout
    setter = Mock(wraps=original)
    monkeypatch.setattr(profiles, "set_card_layout", setter)
    RegionEditor._save_card_layout(e)
    success = target == "new" or answer is True
    assert e.zones == points
    assert dialog.call_count == (0 if target == "new" else 1)
    if target != "new":
        title, text = dialog.call_args.args
        assert title == ("La calibración ya existe" if target == "occupied" else "El perfil ya existe")
        assert '"Target"' in text and APP_MODE_LABELS[mode] in text and "320x180" in text
        assert "No: usar otro nombre" in text and "Cancelar" in text
    if success:
        setter.assert_called_once()
        assert setter.call_args.kwargs["overwrite"] == (target == "occupied")
        e.on_save.assert_called_once_with()
        e.destroy.assert_called_once_with()
        e._log_saved.assert_called_once()
        saved = profiles.get_card_layout(RES, mode, profile="Target")
        assert saved.reference_source == e.capture.calibration_context
        assert profiles.active_name() == "Target"
    else:
        setter.assert_not_called()
        e.on_save.assert_not_called()
        e.destroy.assert_not_called()
        e._log_saved.assert_not_called()
        assert isolated_store.read_bytes() == before
    if answer is False and target != "new":
        e.profile_entry.focus_set.assert_called_once()
        e.profile_entry.selection_range.assert_called_once_with(0, "end")
    else:
        e.profile_entry.focus_set.assert_not_called()
        e.profile_entry.selection_range.assert_not_called()
    error.assert_not_called()


def test_choosing_another_name_requires_a_new_attempt(monkeypatch):
    seed({"individual": payload()})
    e = fake_editor()
    dialog = Mock(return_value=False)
    monkeypatch.setattr(editor_module.messagebox, "askyesnocancel", dialog)
    RegionEditor._save_card_layout(e)
    assert not profiles.profile_exists("New")
    e.profile_var.get = lambda: " New "
    RegionEditor._save_card_layout(e)
    assert profiles.profile_exists("New")
    assert dialog.call_count == 1
    e.on_save.assert_called_once()


@pytest.mark.parametrize("answer", [True, False, None])
def test_race_requires_fresh_confirmation_and_bounded_retry(monkeypatch, answer):
    e = fake_editor()
    original = profiles.set_card_layout
    attempts = []

    def racing(layout, **kwargs):
        attempts.append(kwargs["overwrite"])
        if len(attempts) == 1:
            original(layout, profile="Target")  # simulated intervening writer
        return original(layout, **kwargs)

    monkeypatch.setattr(profiles, "set_card_layout", racing)
    dialog, error = Mock(return_value=answer), Mock()
    monkeypatch.setattr(editor_module.messagebox, "askyesnocancel", dialog)
    monkeypatch.setattr(editor_module.messagebox, "showerror", error)
    RegionEditor._save_card_layout(e)
    assert attempts == ([False, True] if answer is True else [False])
    dialog.assert_called_once()
    assert dialog.call_args.args[0] == "La calibración ya existe"
    assert e.on_save.call_count == int(answer is True)
    assert e.destroy.call_count == int(answer is True)
    error.assert_not_called()


@pytest.mark.parametrize("failure", ["query_read", "profile_read", "write", "verify_read", "verify_missing", "verify_mismatch", "repeat_conflict"])
def test_errors_do_not_close_or_report_success(monkeypatch, failure):
    e = fake_editor()
    dialog, error = Mock(return_value=True), Mock()
    monkeypatch.setattr(editor_module.messagebox, "askyesnocancel", dialog)
    monkeypatch.setattr(editor_module.messagebox, "showerror", error)
    if failure == "query_read":
        monkeypatch.setattr(profiles, "layout_target_exists", Mock(side_effect=OSError("read blocked")))
    elif failure == "profile_read":
        monkeypatch.setattr(profiles, "profile_exists", Mock(side_effect=OSError("profile read blocked")))
    elif failure == "write":
        monkeypatch.setattr(profiles, "_write", Mock(side_effect=OSError("disk full")))
    elif failure == "verify_read":
        monkeypatch.setattr(profiles, "get_card_layout", Mock(side_effect=OSError("verification read blocked")))
    elif failure.startswith("verify"):
        monkeypatch.setattr(profiles, "get_card_layout", Mock(return_value=
            None if failure == "verify_missing" else layout(changed=True)))
    elif failure == "repeat_conflict":
        setter = Mock(side_effect=profiles.ProfileConflictError("conflict"))
        monkeypatch.setattr(profiles, "set_card_layout", setter)
    RegionEditor._save_card_layout(e)
    error.assert_called_once()
    e.on_save.assert_not_called()
    e.destroy.assert_not_called()
    e._log_saved.assert_not_called()
    e._log_save_error.assert_called_once()
    if failure == "repeat_conflict":
        assert setter.call_count == 2 and dialog.call_count == 1
        assert error.call_args.args[0] == "Conflicto de calibración"


def test_replace_failure_keeps_original_json(monkeypatch, isolated_store):
    profiles.set_card_layout(layout(), profile="Target")
    before = isolated_store.read_bytes()
    original = Path.replace

    def denied(path, *args, **kwargs):
        if path == isolated_store.with_suffix(".json.tmp"):
            raise OSError("replace failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(OSError):
        profiles.set_card_layout(layout(changed=True), profile="Target", overwrite=True)
    assert isolated_store.read_bytes() == before
