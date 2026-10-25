"""Exact-resolution table zones: geometry and persistence, not recognition."""
import json

import pytest

from lib.common import constants
from lib.logic import region_profiles
from lib.logic.card_layout import SEVEN_SEATS, SINGLE_SEAT, TABLE, CardLayout


def rect(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def payload(mode=TABLE):
    return {"mode": mode, "resolution": [320, 180],
            "table": rect(5, 5, 315, 175), "mine": rect(20, 100, 100, 160),
            "dealer": rect(100, 20, 200, 60)}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path / "profiles")


@pytest.mark.parametrize("mode", [TABLE, SINGLE_SEAT])
def test_owners_come_from_one_native_coordinate_system(mode):
    layout = CardLayout.from_payload(payload(mode), (320, 180))
    assert layout.owner_at(50, 120) == "mine"
    assert layout.owner_at(150, 40) == "dealer"
    assert layout.owner_at(250, 100) == "other"
    assert layout.owner_at(0, 0) is None
    assert layout.owner_at(20, 100) == "mine"  # boundary is included


@pytest.mark.parametrize("change,match", [
    ({"mine": rect(0, 100, 100, 160)}, "dentro de Mesa"),
    ({"dealer": rect(50, 120, 150, 170)}, "superponerse"),
    ({"dealer": rect(100, 100, 200, 160)}, "tocarse"),
    ({"table": rect(-1, 5, 315, 175)}, "fotograma"),
    ({"table": [[5, 5], [315, 175], [315, 5], [5, 175]]}, "cruza"),
    ({"mine": [[20, 100], [40, 100], [80, 100]]}, "área"),
    ({"mine": [[20, 100], [40, float("nan")], [80, 160]]}, "finitas"),
    ({"mine": []}, "Dibujá Mi asiento"),
    ({"mode": "unknown"}, "modo"),
])
def test_bad_zones_are_rejected(change, match):
    with pytest.raises(ValueError, match=match):
        CardLayout.from_payload(payload() | change, (320, 180))


def test_concave_table_rejects_edge_leaving_table_even_with_vertices_inside():
    data = payload() | {"table": [[5, 5], [315, 5], [315, 175], [200, 175],
                                  [200, 80], [120, 80], [120, 175], [5, 175]],
                        "mine": rect(90, 100, 230, 150)}
    with pytest.raises(ValueError, match="dentro de Mesa"):
        CardLayout.from_payload(data, (320, 180))


def test_modes_and_existing_profiles_survive_save_and_exact_reload(store):
    old = {"players": [rect(i * 40, 90, i * 40 + 30, 150) for i in range(7)],
           "dealer": [100, 20, 200, 60]}
    region_profiles.set_regions((320, 180), old, profile="Keep")
    before = json.loads(region_profiles.store_path().read_text(encoding="utf-8"))
    context = {"kind": "video_frame", "frame_index": 2, "resolution": [320, 180]}
    for mode in (TABLE, SINGLE_SEAT):
        region_profiles.set_card_layout(CardLayout.from_payload(payload(mode), (320, 180)),
                                        profile="Keep", source_context=context)
    assert region_profiles.get_regions((320, 180), profile="Keep") == old
    for mode in (TABLE, SINGLE_SEAT):
        loaded = region_profiles.get_card_layout((320, 180), mode, profile="Keep")
        assert loaded.owner_at(50, 120) == "mine"
        assert loaded.reference_source == context
    assert region_profiles.get_card_layout((640, 360), TABLE, profile="Keep") is None
    assert region_profiles.get_card_layout((320, 180), TABLE, profile="Missing") is None
    assert region_profiles.get_regions((320, 180), profile="Default") is None
    assert region_profiles.layout_mode((320, 180), profile="Keep") == SINGLE_SEAT
    region_profiles.set_regions((320, 180), old, profile="Keep")
    assert region_profiles.layout_mode((320, 180), profile="Keep") == SEVEN_SEATS
    assert region_profiles.get_card_layout((320, 180), TABLE, profile="Keep") is not None
    after = json.loads(region_profiles.store_path().read_text(encoding="utf-8"))
    assert after["profiles"]["Default"] == before["profiles"]["Default"]


def test_wrong_resolution_is_never_stretched():
    with pytest.raises(ValueError, match="resolución"):
        CardLayout.from_payload(payload(), (640, 360))


def test_invalid_save_keeps_existing_bytes(store):
    region_profiles.set_card_layout(CardLayout.from_payload(payload(), (320, 180)), profile="Keep")
    before = region_profiles.store_path().read_bytes()
    with pytest.raises(ValueError):
        CardLayout.from_payload(payload() | {"mine": rect(0, 0, 100, 100)}, (320, 180))
    assert region_profiles.store_path().read_bytes() == before


def test_malformed_mode_storage_is_rejected_without_falling_back(store):
    region_profiles.set_card_layout(CardLayout.from_payload(payload(), (320, 180)), profile="Keep")
    path = region_profiles.store_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["profiles"]["Keep"]["layouts"]["320x180"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="formato inválido"):
        region_profiles.get_card_layout((320, 180), TABLE, profile="Keep")
    with pytest.raises(ValueError, match="formato inválido"):
        region_profiles.layout_mode((320, 180), profile="Keep")
    assert path.read_bytes() == before
