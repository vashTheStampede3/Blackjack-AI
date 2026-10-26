"""Isolated JSON mode persistence and simulated storage failures."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from lib.common import constants
from lib.logic import region_profiles as profiles
from lib.logic.card_layout import CardLayout


RES = (320, 180)
KEY = "320x180"


def payload(mode="individual", resolution=RES):
    data = {"mode": mode, "resolution": list(resolution),
            "mine": [[20, 100], [100, 100], [100, 160], [20, 160]],
            "dealer": [[100, 20], [200, 20], [200, 60], [100, 60]]}
    if mode in ("multiplayer", "table", "single_seat"):
        data["table"] = [[5, 85], [315, 85], [315, 175], [5, 175]]
    return data


def layout(mode="individual", resolution=RES):
    return CardLayout.from_payload(payload(mode, resolution), resolution)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    return profiles.store_path()


def seed(modes=None):
    data = profiles._empty()
    if modes is not None:
        data["profiles"]["Default"]["layouts"][KEY] = modes
    profiles.store_path().write_text(json.dumps(data), encoding="utf-8")
    return data


def read():
    return json.loads(profiles.store_path().read_text(encoding="utf-8"))


@pytest.mark.parametrize("selection,expected", [
    ("individual", "individual"), ("multiplayer", "multiplayer"),
    ("single_seat", "individual"), ("table", "multiplayer"),
    ("seven_seats", "individual"),
])
def test_selection_is_read_only_and_not_calibration(store, selection, expected):
    seed({"selected_mode": selection})
    before = store.read_bytes()
    assert profiles.layout_mode(RES) == expected
    assert not profiles.has_card_layout(RES, expected)
    assert store.read_bytes() == before


def test_fresh_selection_does_not_create_files(store):
    assert profiles.layout_mode(RES) == "individual"
    assert not profiles.has_card_layout(RES, "individual")
    assert not store.exists()


@pytest.mark.parametrize("selection", ["unknown", None, ""])
def test_invalid_selection(selection):
    seed({"selected_mode": selection})
    with pytest.raises(ValueError):
        profiles.layout_mode(RES)


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_save_load_exact_geometry_reference_and_official_json(store, mode):
    source = {"kind": "video_frame", "path": "synthetic.mp4", "frame_index": 4}
    profiles.set_card_layout(layout(mode), profile="Mesa principal", source_context=source)
    recovered = profiles.get_card_layout(RES, mode)
    assert recovered.mode == mode
    assert recovered.to_payload() == payload(mode) | {"reference_source": source}
    assert profiles.has_card_layout(RES, mode)
    assert profiles.active_name() == "Mesa principal"
    data = read()
    prof = data["profiles"]["Mesa principal"]
    assert datetime.fromisoformat(prof["saved"])
    assert set(prof["layouts"][KEY]) == {"selected_mode", mode}
    assert prof["layouts"][KEY]["selected_mode"] == mode
    assert profiles.get_card_layout((640, 360), mode) is None
    assert not profiles.has_card_layout((640, 360), mode)


def test_both_modes_other_resolution_profiles_and_auxiliary_data_survive():
    data = seed()
    prof = data["profiles"]["Default"]
    for kind in ("ocr", "controls", "anchors", "source_contexts", "regions"):
        prof[kind][KEY] = {"preserve": kind}
    prof["layouts"]["640x360"] = {"single_seat": payload("single_seat", (640, 360))}
    data["profiles"]["Other"] = deepcopy(prof)
    profiles.store_path().write_text(json.dumps(data), encoding="utf-8")
    profiles.set_card_layout(layout())
    profiles.set_card_layout(layout("multiplayer"))
    result = read()
    assert result["profiles"]["Other"] == data["profiles"]["Other"]
    saved = result["profiles"]["Default"]
    for kind in ("ocr", "controls", "anchors", "source_contexts", "regions"):
        assert saved[kind] == prof[kind]
    assert saved["layouts"]["640x360"] == prof["layouts"]["640x360"]
    assert set(saved["layouts"][KEY]) == {"selected_mode", "individual", "multiplayer"}
    assert profiles.get_card_layout(RES, "individual") == layout()
    assert profiles.get_card_layout(RES, "multiplayer") == layout("multiplayer")
    assert profiles.set_active("Other")
    assert profiles.active_name() == "Other"
    assert not profiles.set_active("Missing")


@pytest.mark.parametrize("alias,mode", [("single_seat", "individual"), ("table", "multiplayer")])
def test_legacy_read_and_localized_cleanup(store, alias, mode):
    other = "table" if alias == "single_seat" else "single_seat"
    seed({alias: payload(alias), other: payload(other), "selected_mode": alias})
    before = store.read_bytes()
    assert profiles.get_card_layout(RES, mode).mode == mode
    assert profiles.get_card_layout(RES, alias).mode == mode
    assert store.read_bytes() == before
    with pytest.raises(profiles.ProfileConflictError):
        profiles.set_card_layout(layout(mode))
    assert store.read_bytes() == before
    # This test deliberately replaces the compatible historical calibration.
    profiles.set_card_layout(layout(mode), overwrite=True)
    modes = read()["profiles"]["Default"]["layouts"][KEY]
    assert alias not in modes
    assert other in modes
    assert modes[mode] == payload(mode)
    assert modes["selected_mode"] == mode


@pytest.mark.parametrize("mode,alias", [("individual", "single_seat"), ("multiplayer", "table")])
def test_official_priority_and_no_corruption_fallback(mode, alias):
    historical = payload(alias)
    historical["mine"][0][0] = 21
    seed({mode: payload(mode), alias: historical})
    assert profiles.get_card_layout(RES, mode).to_payload() == payload(mode)
    seed({mode: None, alias: historical})
    with pytest.raises(ValueError):
        profiles.get_card_layout(RES, mode)
    assert not profiles.has_card_layout(RES, mode)
    assert profiles.list_profiles()[0]["card_resolutions"] == []


def test_seven_seats_is_not_a_dynamic_layout():
    seed({"selected_mode": "seven_seats", "seven_seats": {"players": []}})
    assert profiles.layout_mode(RES) == "individual"
    assert not profiles.has_card_layout(RES, "individual")
    assert not profiles.has_card_layout(RES, "multiplayer")
    with pytest.raises(ValueError, match="migración"):
        profiles.get_card_layout(RES, "seven_seats")


@pytest.mark.parametrize("kind", ["ocr", "controls", "anchors", "regions"])
def test_auxiliary_only_is_not_card_layout_but_is_general_calibration(kind):
    data = seed()
    data["profiles"]["Default"][kind][KEY] = {"example": [1, 2, 3, 4]}
    profiles.store_path().write_text(json.dumps(data), encoding="utf-8")
    assert profiles.has_calibration(RES)
    assert not profiles.has_card_layout(RES, "individual")


def test_normal_reads_do_not_mutate_file_or_active_profile(store):
    profiles.set_card_layout(layout(), profile="Named")
    profiles.set_active("Default")
    before = store.read_bytes()
    assert profiles.get_card_layout(RES, "individual", profile="Named")
    assert profiles.has_card_layout(RES, "individual", profile="Named")
    assert profiles.get_card_layout(RES, "individual", profile="Missing") is None
    assert profiles.layout_mode(RES, profile="Missing") == "individual"
    assert not profiles.has_card_layout(RES, "individual", profile="Missing")
    profiles.list_profiles()
    assert profiles.active_name() == "Default"
    assert store.read_bytes() == before
    assert "Missing" not in read()["profiles"]


@pytest.mark.parametrize("corrupt", [None, {}, {"mode": "individual"}, payload("multiplayer")])
def test_corrupt_geometry_is_unavailable_but_explicit_load_raises(corrupt):
    seed({"individual": corrupt})
    assert not profiles.has_card_layout(RES, "individual")
    with pytest.raises(ValueError):
        profiles.get_card_layout(RES, "individual")


def test_list_validates_geometry_deduplicates_and_preserves_summary():
    seed({"individual": payload(), "multiplayer": payload("multiplayer")})
    profiles.set_card_layout(layout(), profile="Zulu")
    profiles.set_card_layout(layout(), profile="Alpha")
    data = read()
    prof = data["profiles"]["Default"]
    prof["layouts"].update({"640x360": {"single_seat": payload("single_seat", (640, 360))},
                            "800x600": {"table": payload("table", (800, 600))},
                            "900x600": {"selected_mode": "individual"},
                            "1000x600": {"individual": {}},
                            "1100x600": {"seven_seats": {}}, "invalid": {"individual": payload()}})
    prof["ocr"]["1920x1080"] = {"balance": [1, 2, 3, 4]}
    profiles.store_path().write_text(json.dumps(data), encoding="utf-8")
    items = profiles.list_profiles()
    assert [p["name"] for p in items] == ["Default", "Alpha", "Zulu"]
    assert items[0]["card_resolutions"] == [KEY, "640x360", "800x600"]
    assert profiles.calibration_resolution_summary(items[0]) == (
        (KEY, "640x360", "800x600"), ("1920x1080",))


def test_invalid_layout_does_not_touch_original(store):
    seed()
    before = store.read_bytes()
    invalid = CardLayout("individual", RES, None, (), ())
    with pytest.raises(ValueError):
        profiles.set_card_layout(invalid)
    assert store.read_bytes() == before


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_embedded_reference_is_preserved(mode):
    data = payload(mode) | {"reference_source": {"kind": "video_frame", "frame_index": 9}}
    profiles.set_card_layout(CardLayout.from_payload(data, RES))
    assert profiles.get_card_layout(RES, mode).to_payload() == data


def test_corrupt_resolution_container_and_mismatched_payload():
    seed([])
    with pytest.raises(ValueError, match="formato inválido"):
        profiles.get_card_layout(RES, "individual")
    assert not profiles.has_card_layout(RES, "individual")
    seed({"individual": payload("individual", (640, 360))})
    with pytest.raises(ValueError, match="resolución"):
        profiles.get_card_layout(RES, "individual")


@pytest.mark.parametrize("mode,alias", [("individual", "single_seat"), ("multiplayer", "table")])
def test_alias_cleanup_never_spreads_to_other_profiles_or_resolutions(mode, alias):
    data = seed({alias: payload(alias)})
    prof = data["profiles"]["Default"]
    prof["layouts"]["640x360"] = {alias: payload(alias, (640, 360))}
    data["profiles"]["Other"] = deepcopy(prof)
    profiles.store_path().write_text(json.dumps(data), encoding="utf-8")
    before = profiles.store_path().read_bytes()
    with pytest.raises(profiles.ProfileConflictError):
        profiles.set_card_layout(layout(mode))
    assert profiles.store_path().read_bytes() == before
    # Authorize only the replacement whose cleanup locality is under test.
    profiles.set_card_layout(layout(mode), overwrite=True)
    saved = read()
    assert saved["profiles"]["Other"] == data["profiles"]["Other"]
    assert saved["profiles"]["Default"]["layouts"]["640x360"] == prof["layouts"]["640x360"]
    assert alias not in saved["profiles"]["Default"]["layouts"][KEY]


def test_read_error_propagates_and_blocks_layout_write(store, monkeypatch):
    seed({"individual": payload()})
    before = store.read_bytes()
    original = Path.read_text

    def blocked(path, *args, **kwargs):
        if path == store:
            raise PermissionError("simulated temporary lock")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", blocked)
    with pytest.raises(OSError):
        profiles.set_card_layout(layout("multiplayer"))
    with pytest.raises(OSError):
        profiles.has_card_layout(RES, "individual")
    with pytest.raises(OSError):
        profiles.get_card_layout(RES, "individual")
    assert store.read_bytes() == before


@pytest.mark.parametrize("failure", ["temporary_write", "replace"])
def test_atomic_write_failures_preserve_original(store, monkeypatch, failure):
    seed({"individual": payload()})
    before = store.read_bytes()
    method = "write_text" if failure == "temporary_write" else "replace"
    original = getattr(Path, method)

    def blocked(path, *args, **kwargs):
        if path == store.with_suffix(".json.tmp"):
            raise OSError("simulated disk failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, blocked)
    with pytest.raises(OSError, match="simulated disk failure"):
        profiles.set_card_layout(layout("multiplayer"), profile="New")
    assert store.read_bytes() == before


def test_single_snapshot_write_with_existing_lock(store, monkeypatch):
    seed({"individual": payload()})
    original_load = profiles._load
    calls = []
    original_write = profiles._write

    def load():
        calls.append("read")
        return original_load()

    def write(data):
        assert profiles._LOCK._is_owned()
        calls.append("write")
        original_write(data)

    monkeypatch.setattr(profiles, "_load", load)
    monkeypatch.setattr(profiles, "_write", write)
    profiles.set_card_layout(layout("multiplayer"))
    assert calls == ["read", "write"]
    assert set(read()["profiles"]["Default"]["layouts"][KEY]) == {
        "individual", "multiplayer", "selected_mode"}


def test_reentrant_simulated_writer_is_serialized_without_lost_updates(monkeypatch):
    """Schedule a competing operation at lock entry without creating threads."""
    seed()
    real_lock = profiles._LOCK
    pending = [True]

    class ScheduledLock:
        def __enter__(self):
            if pending:
                pending.pop()
                profiles.set_card_layout(layout("multiplayer"))
            real_lock.acquire()
            return self

        def __exit__(self, *args):
            real_lock.release()

    monkeypatch.setattr(profiles, "_LOCK", ScheduledLock())
    profiles.set_card_layout(layout())
    assert profiles.has_card_layout(RES, "individual")
    assert profiles.has_card_layout(RES, "multiplayer")
