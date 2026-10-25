"""Native-pixel mode geometry, using only in-memory payloads."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from lib.logic.card_layout import CardLayout


RESOLUTION = (320, 180)


def rect(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def payload(mode="individual"):
    data = {"mode": mode, "resolution": list(RESOLUTION),
            "mine": rect(20, 100, 100, 160),
            "dealer": rect(100, 20, 200, 60)}
    if mode in ("multiplayer", "table", "single_seat"):
        data["table"] = rect(5, 85, 315, 175)
    return data


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_valid_layout_and_exact_serialization_round_trip(mode):
    data = payload(mode)
    layout = CardLayout.from_payload(data, RESOLUTION)
    assert layout.mode == mode
    assert layout.resolution == RESOLUTION
    if mode == "individual":
        assert layout.table is None
        assert set(layout.to_payload()) == {"mode", "resolution", "mine", "dealer"}
    else:
        assert layout.table == tuple(map(tuple, data["table"]))
        assert set(layout.to_payload()) == {"mode", "resolution", "table", "mine", "dealer"}
    assert layout.to_payload() == data
    assert CardLayout.from_payload(layout.to_payload(), RESOLUTION) == layout
    with pytest.raises(FrozenInstanceError):
        layout.mode = "unknown"


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("point,owner", [
    ((50, 120), "mine"), ((150, 40), "dealer"),
    ((20, 100), "mine"), ((100, 20), "dealer"), ((0, 0), None),
])
def test_owner_in_native_coordinates_including_boundaries(mode, point, owner):
    assert CardLayout.from_payload(payload(mode), RESOLUTION).owner_at(*point) == owner


def test_individual_has_no_other_owner_anywhere_on_frame():
    layout = CardLayout.from_payload(payload(), RESOLUTION)
    assert layout.owner_at(250, 120) is None
    assert {layout.owner_at(x, y) for x in range(321) for y in range(181)} == {
        None, "mine", "dealer"}


def test_multiplayer_other_and_separate_dealer():
    layout = CardLayout.from_payload(payload("multiplayer"), RESOLUTION)
    assert layout.owner_at(250, 120) == "other"
    assert layout.owner_at(150, 40) == "dealer"  # outside the players' table
    assert layout.owner_at(250, 40) is None


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("zone", ["mine", "dealer"])
def test_missing_required_zone(mode, zone):
    data = payload(mode)
    del data[zone]
    with pytest.raises(ValueError):
        CardLayout.from_payload(data, RESOLUTION)


def test_multiplayer_requires_table():
    data = payload("multiplayer")
    del data["table"]
    with pytest.raises(ValueError):
        CardLayout.from_payload(data, RESOLUTION)


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("dealer", [rect(50, 120, 150, 170), rect(100, 100, 200, 160)])
def test_mine_and_dealer_cannot_overlap_or_touch(mode, dealer):
    with pytest.raises(ValueError, match="tocarse ni superponerse"):
        CardLayout.from_payload(payload(mode) | {"dealer": dealer}, RESOLUTION)


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("mine", [
    [[20, 100], [100, 160], [100, 100], [20, 160]],
    rect(-1, 100, 100, 160),
    [[20, 100], [40, float("nan")], [80, 160]],
    [[20, 100], [40, float("inf")], [80, 160]],
    [[20, 100], [40, 100], [80, 100]],
    [[20, 100], [40, 120]],
])
def test_invalid_polygons(mode, mine):
    with pytest.raises(ValueError):
        CardLayout.from_payload(payload(mode) | {"mine": mine}, RESOLUTION)


@pytest.mark.parametrize("table", [None, [], rect(0, 0, 320, 180)])
def test_explicit_individual_rejects_any_table_key(table):
    with pytest.raises(ValueError, match="individual.*table"):
        CardLayout.from_payload(payload() | {"table": table}, RESOLUTION)


def test_only_multiplayer_requires_mine_inside_table():
    mine = rect(0, 100, 100, 160)
    assert CardLayout.from_payload(payload() | {"mine": mine}, RESOLUTION).table is None
    with pytest.raises(ValueError, match="dentro de Mesa"):
        CardLayout.from_payload(payload("multiplayer") | {"mine": mine}, RESOLUTION)


@pytest.mark.parametrize("old,new", [("single_seat", "individual"), ("table", "multiplayer")])
def test_legacy_read_normalizes_and_writes_only_current_modes(old, new):
    data = payload(old)
    original = deepcopy(data)
    layout = CardLayout.from_payload(data, RESOLUTION)
    assert layout.mode == new
    assert layout.to_payload()["mode"] == new
    assert data == original
    if new == "individual":
        assert layout.table is None
        assert "table" not in layout.to_payload()


@pytest.mark.parametrize("table", [None, "invalid historical geometry"])
def test_single_seat_ignores_unused_historical_table(table):
    layout = CardLayout.from_payload(payload("single_seat") | {"table": table}, RESOLUTION)
    assert layout.mode == "individual"
    assert layout.table is None


def test_seven_seats_requires_migration():
    with pytest.raises(ValueError, match="migración explícita"):
        CardLayout.from_payload(payload("seven_seats"), RESOLUTION)


@pytest.mark.parametrize("data", [None, [], "invalid", 3, {}, {"mode": None}, {"mode": "unknown"}])
def test_invalid_payload(data):
    with pytest.raises(ValueError):
        CardLayout.from_payload(data, RESOLUTION)


@pytest.mark.parametrize("resolution", [(640, 360), None, (), (320,), (320, 0), (320, True), (320.0, 180)])
def test_invalid_or_incompatible_resolution(resolution):
    with pytest.raises(ValueError, match="resolución"):
        CardLayout.from_payload(payload(), resolution)


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_reference_source_and_payload_are_independently_copied(mode):
    data = payload(mode) | {"reference_source": {"kind": "video_frame", "frame": {"index": 4}}}
    original = deepcopy(data)
    layout = CardLayout.from_payload(data, RESOLUTION)
    assert data == original
    assert layout.reference_source == original["reference_source"]
    data["reference_source"]["frame"]["index"] = 9
    data["mine"][0][0] = 99
    assert layout.to_payload() == original
    exported = layout.to_payload()
    exported["reference_source"]["frame"]["index"] = 12
    exported["mine"][0][0] = 88
    assert layout.to_payload() == original


def test_valid_concave_individual_polygon():
    data = payload() | {"mine": [[20, 100], [100, 100], [100, 120],
                                 [50, 120], [50, 160], [20, 160]]}
    layout = CardLayout.from_payload(data, RESOLUTION)
    assert layout.owner_at(30, 140) == "mine"
    assert layout.owner_at(80, 140) is None
    assert layout.to_payload() == data


def test_concave_multiplayer_table_and_containment_across_notch():
    data = payload("multiplayer") | {
        "table": [[5, 85], [315, 85], [315, 175], [200, 175],
                  [200, 120], [120, 120], [120, 175], [5, 175]]}
    layout = CardLayout.from_payload(data, RESOLUTION)
    assert layout.owner_at(250, 150) == "other"
    assert layout.owner_at(150, 150) is None
    assert layout.to_payload() == data
    # Every vertex is inside; the lower edge crosses the concave gap.
    with pytest.raises(ValueError, match="dentro de Mesa"):
        CardLayout.from_payload(data | {"mine": rect(100, 100, 220, 160)}, RESOLUTION)


def test_dealer_priority_when_inside_multiplayer_table():
    layout = CardLayout.from_payload(
        payload("multiplayer") | {"table": rect(5, 5, 315, 175)}, RESOLUTION)
    assert layout.owner_at(150, 40) == "dealer"
