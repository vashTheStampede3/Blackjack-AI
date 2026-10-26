"""Exact-resolution table zones: geometry and persistence, not recognition."""
import json

import pytest

from lib.common import constants
from lib.logic import region_profiles
from lib.logic.app_modes import INDIVIDUAL, MULTIPLAYER
from lib.logic.card_layout import SEVEN_SEATS, SINGLE_SEAT, TABLE, CardLayout
from lib.logic.card_layout import TableGroupTracker


def rect(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def payload(mode=MULTIPLAYER):
    data = {"mode": mode, "resolution": [320, 180],
            "mine": rect(20, 100, 100, 160), "dealer": rect(100, 20, 200, 60)}
    if mode in (MULTIPLAYER, TABLE, SINGLE_SEAT):
        data["table"] = rect(5, 5, 315, 175)
    return data


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path / "profiles")


@pytest.mark.parametrize("mode", [INDIVIDUAL, MULTIPLAYER])
def test_owners_come_from_one_native_coordinate_system(mode):
    layout = CardLayout.from_payload(payload(mode), (320, 180))
    assert layout.owner_at(50, 120) == "mine"
    assert layout.owner_at(150, 40) == "dealer"
    assert layout.owner_at(250, 100) == ("other" if mode == MULTIPLAYER else None)
    assert layout.owner_at(0, 0) is None
    assert layout.owner_at(20, 100) == "mine"  # boundary is included
    assert layout.owner_at(100, 160) == "mine"
    assert layout.owner_at(100, 20) == "dealer"
    assert layout.owner_at(320, 180) is None
    if mode == INDIVIDUAL:
        assert {layout.owner_at(x, y) for x in range(321) for y in range(181)} == {
            None, "mine", "dealer"}
    else:
        assert layout.owner_at(315, 175) == "other"  # table boundary is included


@pytest.mark.parametrize("mode", [INDIVIDUAL, MULTIPLAYER])
def test_dealer_graphic_outside_table_is_saved_and_tagged_without_expanding_table(store, mode):
    data = payload(mode)
    if mode == MULTIPLAYER:
        data["table"] = rect(5, 85, 315, 175)
    layout = CardLayout.from_payload(data, (320, 180))
    region_profiles.set_card_layout(layout, profile="Separate dealer")
    loaded = region_profiles.get_card_layout((320, 180), mode, profile="Separate dealer")
    assert loaded.mode == mode
    assert loaded.resolution == (320, 180)
    assert loaded.to_payload() == data
    if mode == INDIVIDUAL:
        assert layout.table is None and loaded.table is None
        assert "table" not in loaded.to_payload()
        assert set(loaded.to_payload()) == {"mode", "resolution", "mine", "dealer"}
    else:
        assert loaded.to_payload()["table"] == data["table"]
        assert loaded.table == tuple(map(tuple, data["table"]))
        with pytest.raises(ValueError, match="dentro de Mesa"):
            CardLayout.from_payload(data | {"mine": rect(0, 100, 100, 160)}, (320, 180))
    assert loaded.to_payload()["mine"] == data["mine"]
    assert loaded.to_payload()["dealer"] == data["dealer"]
    assert loaded.owner_at(150, 40) == "dealer"
    assert loaded.owner_at(50, 120) == "mine"
    assert loaded.owner_at(250, 120) == ("other" if mode == MULTIPLAYER else None)
    assert loaded.owner_at(250, 40) is None  # gap/interface between the two sources


def test_dealer_outside_frame_is_still_rejected():
    with pytest.raises(ValueError, match="fotograma"):
        CardLayout.from_payload(payload() | {"dealer": rect(300, 20, 340, 60)}, (320, 180))


@pytest.mark.parametrize("change,match", [
    ({"mine": rect(0, 100, 100, 160)}, "dentro de Mesa"),
    ({"dealer": rect(50, 120, 150, 170)}, "superponerse"),
    ({"dealer": rect(100, 100, 200, 160)}, "tocarse"),
    ({"table": rect(-1, 5, 315, 175)}, "fotograma"),
    ({"table": [[5, 5], [315, 175], [315, 5], [5, 175]]}, "cruza"),
    ({"mine": [[20, 100], [40, 100], [80, 100]]}, "área"),
    ({"mine": [[20, 100], [40, float("nan")], [80, 160]]}, "finitas"),
    ({"mine": []}, "Dibujá Mi asiento"),
    ({"mode": "unknown"}, r"(?i)modo"),
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
    assert region_profiles.layout_mode((320, 180), profile="Keep") == INDIVIDUAL
    # The UI selection is not an automatic migration of the seven old regions.
    for mode in (INDIVIDUAL, MULTIPLAYER):
        assert not region_profiles.has_card_layout((320, 180), mode, profile="Keep")
        assert region_profiles.get_card_layout((320, 180), mode, profile="Keep") is None
    before = json.loads(region_profiles.store_path().read_text(encoding="utf-8"))
    context = {"kind": "video_frame", "frame_index": 2, "resolution": [320, 180]}
    for mode in (MULTIPLAYER, INDIVIDUAL):
        region_profiles.set_card_layout(CardLayout.from_payload(payload(mode), (320, 180)),
                                        profile="Keep", source_context=context)
    assert region_profiles.get_regions((320, 180), profile="Keep") == old
    saved = json.loads(region_profiles.store_path().read_text(encoding="utf-8"))
    stored = saved["profiles"]["Keep"]["layouts"]["320x180"]
    assert set(stored) == {"selected_mode", INDIVIDUAL, MULTIPLAYER}
    assert stored["selected_mode"] == INDIVIDUAL
    for mode in (INDIVIDUAL, MULTIPLAYER):
        loaded = region_profiles.get_card_layout((320, 180), mode, profile="Keep")
        assert loaded.owner_at(50, 120) == "mine"
        assert loaded.reference_source == context
        assert loaded.mode == stored[mode]["mode"] == mode
        assert loaded.to_payload() == payload(mode) | {"reference_source": context}
        assert (loaded.table is None) == (mode == INDIVIDUAL)
        assert region_profiles.has_card_layout((320, 180), mode, profile="Keep")
        assert region_profiles.get_card_layout((640, 360), mode, profile="Keep") is None
        assert not region_profiles.has_card_layout((640, 360), mode, profile="Keep")
        assert region_profiles.get_card_layout((320, 180), mode, profile="Missing") is None
        original = region_profiles.store_path().read_bytes()
        with pytest.raises(region_profiles.ProfileConflictError):
            region_profiles.set_card_layout(loaded, profile="Keep")
        assert region_profiles.store_path().read_bytes() == original
    for alias, mode in ((SINGLE_SEAT, INDIVIDUAL), (TABLE, MULTIPLAYER)):
        assert region_profiles.get_card_layout((320, 180), alias, profile="Keep").mode == mode
    assert region_profiles.get_regions((320, 180), profile="Default") is None
    assert region_profiles.layout_mode((320, 180), profile="Keep") == INDIVIDUAL
    region_profiles.set_regions((320, 180), old, profile="Keep")
    assert region_profiles.layout_mode((320, 180), profile="Keep") == INDIVIDUAL
    for mode in (INDIVIDUAL, MULTIPLAYER):
        assert region_profiles.get_card_layout((320, 180), mode, profile="Keep").to_payload() == stored[mode]
    with pytest.raises(ValueError, match="migraci"):
        region_profiles.get_card_layout((320, 180), SEVEN_SEATS, profile="Keep")
    after = json.loads(region_profiles.store_path().read_text(encoding="utf-8"))
    assert after["profiles"]["Default"] == before["profiles"]["Default"]
    assert region_profiles.get_regions((320, 180), profile="Keep") == old
    for mode in (INDIVIDUAL, MULTIPLAYER):
        assert after["profiles"]["Keep"]["layouts"]["320x180"][mode] == stored[mode]


@pytest.mark.parametrize("alias,mode", [(SINGLE_SEAT, INDIVIDUAL), (TABLE, MULTIPLAYER)])
def test_compatible_historical_layout_reads_use_official_modes_without_writing(store, alias, mode):
    region_profiles.set_card_layout(CardLayout.from_payload(payload(mode), (320, 180)), profile="Aliases")
    path = region_profiles.store_path()
    saved = json.loads(path.read_text(encoding="utf-8"))
    # Simulate a previously stored compatible layout in this temporary fixture.
    saved["profiles"]["Aliases"]["layouts"]["320x180"] = {
        "selected_mode": alias, alias: payload(alias)}
    path.write_text(json.dumps(saved), encoding="utf-8")
    original = path.read_bytes()
    assert region_profiles.layout_mode((320, 180), profile="Aliases") == mode
    for requested in (alias, mode):
        loaded = region_profiles.get_card_layout((320, 180), requested, profile="Aliases")
        assert loaded.mode == mode
        assert loaded.to_payload() == payload(mode)
        assert (loaded.table is None) == (mode == INDIVIDUAL)
    assert path.read_bytes() == original
    with pytest.raises(region_profiles.ProfileConflictError):
        region_profiles.set_card_layout(loaded, profile="Aliases")
    assert path.read_bytes() == original


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
        region_profiles.get_card_layout((320, 180), MULTIPLAYER, profile="Keep")
    with pytest.raises(ValueError, match="formato inválido"):
        region_profiles.layout_mode((320, 180), profile="Keep")
    assert path.read_bytes() == before


def test_table_groups_are_numbered_right_to_left_then_keep_identity():
    from types import SimpleNamespace
    tracker = TableGroupTracker()
    cards = [SimpleNamespace(seat_id=None, manual_seat_override=None,
        state="confirmed", owner="other", observed_owner="other",
        first_seen=1., last_seen=1., cx=x, cy=100., width=20., height=30.,
        vx=0., vy=0., ambiguous_seat=False, is_rotated_double=False)
        for x in (190., 400., 610., 820.)]
    groups = tracker.update_groups(cards)
    assert [(group.seat_id, group.anchor_x) for group in groups] == [
        ("J1", 820.), ("J2", 610.), ("J3", 400.), ("J4", 190.)]
    extra = SimpleNamespace(seat_id=None, manual_seat_override=None,
        state="confirmed", owner="other", observed_owner="other",
        first_seen=2., last_seen=2., cx=405., cy=101., width=20., height=30.,
        vx=0., vy=0., ambiguous_seat=False, is_rotated_double=False)
    groups = tracker.update_groups(cards + [extra])
    assert extra.seat_id == "J3"
    assert [group.seat_id for group in groups] == ["J1", "J2", "J3", "J4"]


def test_equal_cost_seat_assignment_is_explicitly_ambiguous():
    from types import SimpleNamespace
    tracker = TableGroupTracker()
    cards = [SimpleNamespace(seat_id=None, manual_seat_override=None,
        state="confirmed", owner="other", observed_owner="other",
        first_seen=1., last_seen=1., cx=x, cy=100., width=20., height=30.,
        vx=0., vy=0., ambiguous_seat=False, is_rotated_double=False)
        for x in (210., 290.)]
    tracker.update_groups(cards)
    candidate = SimpleNamespace(seat_id=None, manual_seat_override=None,
        state="confirmed", owner="other", observed_owner="other",
        first_seen=2., last_seen=2., cx=250., cy=100., width=20., height=30.,
        vx=0., vy=0., ambiguous_seat=False, is_rotated_double=False)
    groups = tracker.update_groups(cards + [candidate])
    assert candidate.ambiguous_seat
    assert any("asiento dudoso" in group.display_label for group in groups)
