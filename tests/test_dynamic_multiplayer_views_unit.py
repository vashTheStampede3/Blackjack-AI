"""Dynamic multiplayer presentation/rendering contracts with fake UI objects."""

from copy import deepcopy

import pytest

from lib.logic.table_presentation import build_table_presentation
from tests.test_individual_table_view_unit import (
    card, presentation as individual_presentation, view,
)
from tests.test_mini_table_presentation_unit import mini


def _snapshot(groups, *, dealer=None):
    return {
        "source_mode": "multiplayer",
        "source_profile": "dynamic-profile",
        "source_resolution": [1280, 720],
        "source_context": {"source_id": "capture-1"},
        "seq": 12,
        "round": 4,
        "shoe_id": "shoe-dynamic",
        "card_groups": groups,
        "dealer_cards": dealer or [],
    }


def _group(group_id, *, hero=False, ambiguous=False, cards=None, split=False):
    return {
        "seat_id": group_id,
        "is_hero": hero,
        "is_ambiguous": ambiguous,
        "has_split": split,
        "closed_by_double": False,
        "cards": list(cards or []),
    }


def _groups(count):
    groups = []
    for index in range(count):
        group_id = f"J{index + 1}"
        groups.append(_group(
            group_id,
            hero=index == 0,
            cards=[card(f"physical-{group_id}", "8", owner="mine" if index == 0 else "other")],
        ))
    return groups


@pytest.mark.parametrize("count", range(8))
def test_presentation_contains_only_observed_multiplayer_groups(count):
    source_groups = _groups(count)
    result = build_table_presentation(_snapshot(source_groups), selected_mode="multiplayer", running=True)

    assert [player["id"] for player in result["players"]] == [f"J{i + 1}" for i in range(count)]
    assert result["group_count"] == count
    assert result["group_overflow"] is False
    assert len(result["players"]) == count
    assert sum(len(player["cards"]) for player in result["players"]) == count


def test_more_than_seven_groups_reports_overflow_without_eighth_player():
    result = build_table_presentation(_snapshot(_groups(8)), selected_mode="multiplayer", running=True)

    assert len(result["players"]) == 7
    assert result["group_count"] == 8
    assert result["group_overflow"] is True
    assert result["group_warning"] == ">7 grupos detectados - Revisar calibración"


def test_provisional_ambiguous_and_split_group_metadata_are_preserved():
    cards = [card("tmp-card-1", "8", hand=0, owner="mine"),
             card("tmp-card-2", "8", hand=1, owner="mine")]
    result = build_table_presentation(_snapshot([
        _group("TMP_1", hero=True, ambiguous=True, cards=cards, split=True),
        _group("J3", cards=[card("j3-card", "5", owner="other")]),
    ]), selected_mode="multiplayer", running=True)

    provisional, other = result["players"]
    assert provisional["id"] == provisional["seat_id"] == "TMP_1"
    assert provisional["is_provisional"] is True
    assert provisional["is_ambiguous"] is True
    assert provisional["has_split"] is True
    assert [item["card_id"] for item in provisional["cards"]] == ["tmp-card-1", "tmp-card-2"]
    assert [item["hand"] for item in provisional["cards"]] == [0, 1]
    assert other["id"] == "J3"


@pytest.mark.parametrize("count", range(8))
def test_main_table_renders_observed_multiplayer_players_only(view, count):
    result = build_table_presentation(_snapshot(_groups(count)), selected_mode="multiplayer", running=True)

    view.update(result)

    assert [seat["id"] for seat in view.seats] == [f"J{i + 1}" for i in range(count)]
    assert [widget.card_identity["card_id"] for seat in view.seats for widget in seat["cards"]] == [
        f"physical-J{i + 1}" for i in range(count)
    ]


def test_minimesa_renders_same_group_cards_and_visible_overflow(mini):
    groups = _groups(8)
    result = build_table_presentation(_snapshot(groups, dealer=[card("dealer-1", "A", owner="dealer")]),
                                      selected_mode="multiplayer", running=True)
    mini.update_from_presentation(result)

    shown = [record["card"]["card_id"] for record in mini._drawn_cards]
    assert shown == ["dealer-1", *[f"physical-J{i}" for i in range(1, 8)]]
    assert result["group_warning"] in mini.canvas.texts()
    assert "TMP_1" not in mini.canvas.texts()
    assert not any(str(text).startswith("J8") for text in mini.canvas.texts())


def test_both_views_receive_same_physical_group_order_and_ids(view, mini):
    groups = _groups(3)
    result = build_table_presentation(_snapshot(groups), selected_mode="multiplayer", running=True)
    before = deepcopy(result)

    view.update(result)
    mini.update_from_presentation(result)

    main_ids = [widget.card_identity["card_id"] for seat in view.seats for widget in seat["cards"]]
    mini_ids = [record["card"]["card_id"] for record in mini._drawn_cards if record["card"].get("group_id") != "dealer"]
    assert main_ids == mini_ids == [f"physical-J{i}" for i in range(1, 4)]
    assert [seat["id"] for seat in view.seats] == [player["id"] for player in result["players"]]
    assert result == before


def test_provisional_and_ambiguous_labels_remain_explicit_in_both_views(view, mini):
    groups = [
        _group("TMP_1", hero=True, ambiguous=True,
               cards=[card("tmp-card", "7", owner="mine")]),
        _group("J2", cards=[card("j2-card", "9", owner="other")]),
    ]
    result = build_table_presentation(_snapshot(groups), selected_mode="multiplayer", running=True)

    view.update(result)
    mini.update_from_presentation(result)

    main_labels = [seat["name"].cget("text") for seat in view.seats]
    mini_labels = [str(text) for text in mini.canvas.texts()]
    assert any("TMP_1" in label for label in main_labels + mini_labels)
    assert any("AMBIGUO" in label or "?" in label for label in main_labels + mini_labels)
    assert "tmp-card" in [widget.card_identity["card_id"] for seat in view.seats for widget in seat["cards"]]


def test_dynamic_table_resize_and_dpi_keep_card_identities(view, monkeypatch):
    result = build_table_presentation(_snapshot(_groups(3)), selected_mode="multiplayer", running=True)
    view.update(result)
    cards = [widget for seat in view.seats for widget in seat["cards"]]
    identities = [deepcopy(widget.card_identity) for widget in cards]

    view.canvas.width, view.canvas.height = 1450, 820
    view._relayout()
    view._rescale()

    current = [widget for seat in view.seats for widget in seat["cards"]]
    assert [widget.card_identity for widget in current] == identities
    assert len(view.seats) == 3


def test_individual_presentation_contract_remains_single_player():
    result = individual_presentation(mode="individual")
    assert len(result["players"]) == 1
    assert result["players"][0]["id"] == "hero"
