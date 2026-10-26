"""Public contract tests for the presentation-only table projection."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from lib.logic.table_presentation import build_table_presentation


def _card(card_id="c1", rank="A", *, state="confirmed", owner="mine", **extra):
    return {
        "card_id": card_id, "rank": rank, "suit": "spades", "state": state,
        "reason": None, "hand": 0, "owner": owner, "ambiguous_seat": None,
        "first_seen": 1, "last_seen": 2, "center_norm": [0.5, 0.5],
        "bbox_norm": {"x": .4, "y": .4, "w": .2, "h": .2, "cx": .5, "cy": .5},
        **extra,
    }


def _group(seat_id="J1", *, hero=True, cards=None, **extra):
    return {"seat_id": seat_id, "is_hero": hero, "is_ambiguous": False,
            "has_split": False, "closed_by_double": False, "cards": list(cards or []), **extra}


def _snapshot(*, mode="individual", groups=None, dealer=None, **extra):
    return {"source_mode": mode, "source_profile": "table-profile",
            "source_resolution": [1280, 720], "seq": 4, "round": 9,
            "shoe_id": "shoe-2", "error": None, "source_error": None,
            "dealer": {"card": None, "locked": False, "extras": []},
            "card_groups": list(groups or []), "dealer_cards": list(dealer or []), **extra}


def _ready_individual(**kw):
    groups = kw.pop("groups", [_group(cards=[_card("h1", "A"), _card("h2", "K")])])
    dealer = kw.pop("dealer", [_card("d1", "5", owner="dealer")])
    return _snapshot(groups=groups, dealer=dealer, **kw)


def _hero(out):
    return next(p for p in out["players"] if p["id"] == "hero")


def test_individual_always_has_logical_hero_placeholder_without_group():
    out = build_table_presentation(_snapshot(groups=[]), running=True)
    assert len(out["players"]) == 1
    p = _hero(out)
    assert (p["seat_id"], p["label"], p["is_hero"]) == (None, "Yo", True)
    assert p["cards"] == [] and p["advice"] is None


@pytest.mark.parametrize("snapshot,selected,status", [
    (None, "individual", "waiting"),
    (_snapshot(groups=[], error="bad"), None, "error"),
    (_snapshot(groups=[], source_error="bad"), None, "error"),
    (_snapshot(mode="multiplayer", groups=[]), "individual", "source_mismatch"),
])
def test_individual_placeholder_survives_waiting_stopped_error_and_mismatch(snapshot, selected, status):
    if snapshot is None:
        out = build_table_presentation(snapshot, selected_mode=selected, running=True)
    else:
        out = build_table_presentation(snapshot, selected_mode=selected or "individual", running=True)
    assert out["status"] == status
    assert len(out["players"]) == 1 and _hero(out)["cards"] == []


def test_stopped_individual_has_placeholder_without_old_cards():
    out = build_table_presentation(_ready_individual(), running=False)
    assert out["status"] == "stopped"
    assert len(out["players"]) == 1 and _hero(out)["cards"] == []
    assert _hero(out)["advice"] is None and out["dealer"]["cards"] == []
    assert out["advice_available"] is False


def test_stopped_keeps_valid_sequence_metadata_and_discards_historical_advice():
    snap = _ready_individual(seats=[{"index": 0, "mine": True, "advice": "P", "book_action": "P", "optimal_action": "P"}])
    out = build_table_presentation(snap, running=False)
    assert (out["seq"], out["round_id"], out["shoe_id"]) == (4, 9, "shoe-2")
    assert out["advice_available"] is False and _hero(out)["advice"] is None
    assert _hero(out)["cards"] == [] and out["dealer"]["cards"] == []


@pytest.mark.parametrize("mode", [None, "seven_seats", "unknown", "", "Individual"])
def test_unknown_mode_needs_calibration_without_cards(mode):
    out = build_table_presentation(_snapshot(mode=mode, groups=[_group(cards=[_card()])]), running=True)
    assert out["status"] == "needs_calibration" and out["mode"] is None
    assert out["players"] == [] and out["dealer"]["cards"] == []


def test_unknown_source_mode_can_be_supplied_by_selected_official_mode():
    out = build_table_presentation(_ready_individual(mode=None), selected_mode="individual", running=True)
    assert out["mode"] == "individual" and _hero(out)["is_hero"] is True


def test_missing_source_profile_does_not_mean_invalid_calibration():
    snap = _ready_individual()
    snap["source_profile"] = None
    out = build_table_presentation(snap, selected_mode="individual", running=True)
    assert out["mode"] == "individual"
    assert out["status"] != "needs_calibration"


@pytest.mark.parametrize("field", ["error", "source_error"])
def test_snapshot_errors_clear_cards_but_keep_individual_placeholder(field):
    out = build_table_presentation(_ready_individual(**{field: "capture failed"}), running=True)
    assert out["status"] == "error" and _hero(out)["cards"] == []
    assert out["dealer"]["cards"] == []


def test_explicit_mode_mismatch_clears_cards_and_keeps_individual_placeholder():
    out = build_table_presentation(_ready_individual(mode="multiplayer"), selected_mode="individual", running=True)
    assert out["status"] == "source_mismatch"
    assert _hero(out)["cards"] == [] and out["dealer"]["cards"] == []


def test_explicit_unknown_source_mode_vs_official_selection_is_mismatch():
    out = build_table_presentation(_ready_individual(mode="seven_seats"), selected_mode="individual", running=True)
    assert out["status"] == "source_mismatch"
    assert _hero(out)["cards"] == [] and out["dealer"]["cards"] == []


def test_individual_ready_requires_two_confirmed_hero_cards_and_dealer():
    out = build_table_presentation(_ready_individual(), running=True)
    assert out["status"] == "ready"
    assert len(_hero(out)["cards"]) == 2 and len(out["dealer"]["cards"]) == 1


@pytest.mark.parametrize("cards,dealer", [
    ([], [_card("d", "5", owner="dealer")]),
    ([_card("h", "A")], [_card("d", "5", owner="dealer")]),
    ([_card("h1", "A"), _card("h2", "K")], []),
])
def test_incomplete_table_is_not_ready(cards, dealer):
    out = build_table_presentation(_snapshot(groups=[_group(cards=cards)], dealer=dealer), running=True)
    assert out["status"] != "ready"


@pytest.mark.parametrize("state,reason,expected", [
    ("pending", None, "reading"), ("pending", "low_confidence", "uncertain"),
    ("doubtful", "owner_conflict", "uncertain"), ("uncertain", None, "uncertain"),
    ("rejected", None, "uncertain"),
    ("retired", None, "uncertain"),
])
def test_physical_card_states_are_not_promoted(state, reason, expected):
    raw_rank = None if state in {"pending", "doubtful", "uncertain", "rejected", "retired"} else "A"
    card = _card("h1", raw_rank, state=state, reason=reason,
                 **({"status": "not_a_card"} if state == "rejected" else {}))
    out = build_table_presentation(_snapshot(groups=[_group(cards=[card, _card("h2", "K")])], dealer=[_card("d", "5", owner="dealer")]), running=True)
    assert out["status"] == expected
    assert _hero(out)["cards"][0]["state"] == state
    if state in {"pending", "doubtful", "uncertain", "rejected", "retired"}:
        assert _hero(out)["cards"][0]["rank"] is None


def test_confirmed_card_with_reason_does_not_publish_rank():
    out = build_table_presentation(_snapshot(groups=[_group(cards=[_card("h1", "A", reason="conflict"), _card("h2", "K")])], dealer=[_card("d", "5", owner="dealer")]), running=True)
    assert _hero(out)["cards"][0]["rank"] is None
    assert out["status"] != "ready"


def test_confirmed_card_without_rank_is_not_ready():
    out = build_table_presentation(_snapshot(groups=[_group(cards=[_card("h1", None), _card("h2", "K")])], dealer=[_card("d", "5", owner="dealer")]), running=True)
    assert out["status"] != "ready"


def test_missing_suit_is_none_and_does_not_prevent_ready():
    card = _card("h1", "A")
    card.pop("suit")
    out = build_table_presentation(_snapshot(groups=[_group(cards=[card, _card("h2", "K")])], dealer=[_card("d", "5", owner="dealer")]), running=True)
    assert _hero(out)["cards"][0]["suit"] is None and out["status"] == "ready"


def test_repeated_ranks_keep_physical_ids_and_order():
    out = build_table_presentation(_snapshot(groups=[_group(cards=[_card("h1", "8"), _card("h2", "8")])], dealer=[_card("d", "8", owner="dealer")]), running=True)
    assert [c["rank"] for c in _hero(out)["cards"]] == ["8", "8"]
    assert [c["card_id"] for c in _hero(out)["cards"]] == ["h1", "h2"]


def test_individual_group_without_hero_keeps_placeholder_and_ignores_cards():
    out = build_table_presentation(_snapshot(groups=[_group("J2", hero=False, cards=[_card("x", "A", owner="other")])]), running=True)
    assert len(out["players"]) == 1 and _hero(out)["cards"] == []


def test_individual_hero_is_not_inferred_from_first_group_or_j1():
    groups = [_group("J1", hero=False, cards=[_card("x", "A")]), _group("J5", hero=True, cards=[_card("h", "K")])]
    out = build_table_presentation(_snapshot(groups=groups), running=True)
    assert len(out["players"]) == 1 and _hero(out)["seat_id"] == "J5"
    assert [c["card_id"] for c in _hero(out)["cards"]] == ["h"]


def test_multiple_individual_heroes_are_uncertain_and_not_merged():
    groups = [_group("J2", cards=[_card("a", "A")]), _group("J5", cards=[_card("b", "K")])]
    out = build_table_presentation(_snapshot(groups=groups), running=True)
    assert out["status"] == "uncertain" and len(out["players"]) == 1
    assert _hero(out)["cards"] == []


def test_other_owner_is_uncertain_and_reported_without_second_player():
    out = build_table_presentation(_snapshot(groups=[_group(cards=[_card("x", "A", owner="other")])]), running=True)
    assert out["status"] == "uncertain" and out["issues"]
    assert len(out["players"]) == 1 and _hero(out)["cards"] == []


def test_split_metadata_and_hand_ids_are_preserved():
    out = build_table_presentation(_snapshot(groups=[_group(cards=[_card("a", "A", hand=0), _card("b", "8", hand=1)], has_split=True, closed_by_double=True)]), running=True)
    p = _hero(out)
    assert p["has_split"] is True and p["closed_by_double"] is True
    assert [c["hand"] for c in p["cards"]] == [0, 1]


@pytest.mark.parametrize("n", [0, 1, 3, 7])
def test_multiplayer_preserves_published_groups_in_order(n):
    groups = [_group(f"J{i+1}", hero=(i == 0), cards=[_card(f"c{i}", "A", owner="mine" if i == 0 else "other")]) for i in range(n)]
    out = build_table_presentation(_snapshot(mode="multiplayer", groups=groups), running=True)
    assert [p["seat_id"] for p in out["players"]] == [f"J{i+1}" for i in range(n)]


def test_multiplayer_empty_does_not_fabricate_players():
    out = build_table_presentation(_snapshot(mode="multiplayer"), running=True)
    assert out["players"] == []


def test_multiplayer_temporary_ids_and_order_are_preserved():
    groups = [_group("TMP_1", hero=False), _group("J3", hero=False)]
    out = build_table_presentation(_snapshot(mode="multiplayer", groups=groups), running=True)
    assert [p["id"] for p in out["players"]] == ["TMP_1", "J3"]


def test_multiplayer_over_seven_with_one_hero_is_uncertain_and_reports_excess():
    groups = [_group(f"J{i+1}", hero=(i == 0)) for i in range(8)]
    out = build_table_presentation(_snapshot(mode="multiplayer", groups=groups), running=True)
    assert out["status"] == "uncertain" and len(out["players"]) == 7
    assert out["issues"]


def test_multiplayer_ambiguity_is_exposed_and_uncertain():
    out = build_table_presentation(_snapshot(mode="multiplayer", groups=[_group("J1", is_ambiguous=True)]), running=True)
    assert out["status"] == "uncertain" and out["players"][0]["is_ambiguous"] is True


def test_multiplayer_hero_flag_is_preserved_without_position_inference():
    out = build_table_presentation(_snapshot(mode="multiplayer", groups=[_group("J4", hero=False)]), running=True)
    assert out["players"][0]["is_hero"] is False and out["players"][0]["cards"] == []


def test_multiplayer_multiple_heroes_are_uncertain():
    out = build_table_presentation(_snapshot(mode="multiplayer", groups=[_group("J1"), _group("J2")]), running=True)
    assert out["status"] == "uncertain"


def test_no_hero_with_multiplayer_groups_is_reading():
    out = build_table_presentation(_snapshot(mode="multiplayer", groups=[_group("J2", hero=False)]), running=True)
    assert out["status"] == "reading"


@pytest.mark.parametrize("reading,expected", [
    ({"count_pending": 1}, "reading"), ({"count_uncertain": 1}, "uncertain"),
    ({"dealer_ok": False}, "reading"), ({"present": False}, "reading"),
    ({"inferred_at": 0}, "reading"),
])
def test_real_reading_flags_block_ready(reading, expected):
    base = {"captured_at": 1, "inferred_at": 1, "present": True, "empty": False,
            "seats": {}, "dealer_ok": True, "count_uncertain": 0, "count_pending": 0}
    base.update(reading)
    out = build_table_presentation(_ready_individual(reading=base), running=True)
    assert out["status"] == expected


def test_complete_cards_can_be_ready_when_reading_is_absent():
    assert build_table_presentation(_ready_individual(), running=True)["status"] == "ready"


def test_count_snapshot_is_passed_without_recalculation_or_mutation():
    count = {"running": 3, "true": 0.5, "per_rank": {"Ace": 2, "2": 1},
             "cards_seen": 37, "count_uncertain": False}
    snap = _ready_individual(count=count)
    before = copy.deepcopy(snap)
    build_table_presentation(snap, running=True)
    assert snap == before
    assert snap["count"] == {"running": 3, "true": 0.5, "per_rank": {"Ace": 2, "2": 1},
                             "cards_seen": 37, "count_uncertain": False}


def test_only_dealer_cards_are_used_as_dealer_source():
    snap = _ready_individual(dealer=[])
    snap["dealer"] = {"card": "Ace", "locked": True, "extras": []}
    out = build_table_presentation(snap, running=True)
    assert out["dealer"]["cards"] == [] and out["status"] != "ready"


def test_pending_dealer_rank_is_none_and_dealer_dict_is_not_fallback():
    pending = {"rank": "Ace", "state": "pending", "legacy_ref": {"dealer": True, "slot": 0}}
    snap = _ready_individual(dealer=[pending])
    snap["dealer"] = {"card": "King", "locked": True, "extras": []}
    out = build_table_presentation(snap, running=True)
    assert out["dealer"]["cards"][0]["rank"] is None
    assert out["dealer"]["cards"][0]["state"] == "pending"


def test_multiple_dealer_cards_keep_order_and_duplicate_ranks_without_hidden_cards():
    dealer = [_card("d1", "8", owner="dealer"), _card("d2", "8", owner="dealer")]
    out = build_table_presentation(_snapshot(groups=[_group(cards=[_card("h1", "A"), _card("h2", "K")])], dealer=dealer), running=True)
    assert [(c["card_id"], c["rank"]) for c in out["dealer"]["cards"]] == [("d1", "8"), ("d2", "8")]
    assert "hole_card" not in out["dealer"] and "hidden_cards" not in out["dealer"]


def test_legacy_player_and_dealer_cards_keep_refs_without_inventing_physical_ids():
    legacy = {"state": "confirmed", "rank": "Q", "hand": 1, "suit": "hearts",
              "legacy_ref": {"seat_index": 0, "card_index": 2}, "bbox": [1, 2, 3, 4],
              "cx": 2, "cy": 3, "center_norm": [0.2, 0.3], "confidence": 0.9}
    dealer = {"rank": "A", "state": "confirmed", "legacy_ref": {"dealer": True, "slot": 0}}
    out = build_table_presentation(_snapshot(groups=[_group(cards=[legacy])], dealer=[dealer]), running=True)
    card = _hero(out)["cards"][0]
    assert card["legacy_ref"] == legacy["legacy_ref"] and "card_id" not in card and "owner" not in card
    assert out["dealer"]["cards"][0]["legacy_ref"] == dealer["legacy_ref"]


def test_registry_uncertainty_is_evidence_only_not_hand_reconstruction():
    registry = [{"id": "z", "counted": True, "owner": "mine", "observed_owner": "other",
                 "reason": "owner_conflict", "state": "confirmed", "status": "active",
                 "possible_ids": ["a", "b"], "hand": 0, "ambiguous_seat": True}]
    out = build_table_presentation(_snapshot(groups=[_group(cards=[])], card_registry=registry), running=True)
    assert _hero(out)["cards"] == [] and out["status"] == "uncertain"


def test_active_pending_registry_entry_blocks_ready_even_without_reading_count():
    registry = [{"id": "z", "counted": False, "owner": "mine", "observed_owner": "mine",
                 "reason": None, "state": "pending", "status": "active", "possible_ids": [], "hand": 0}]
    out = build_table_presentation(_ready_individual(card_registry=registry), running=True)
    assert out["status"] == "reading"


def test_card_registry_confirmed_active_does_not_create_cards():
    registry = [{"id": "z", "counted": True, "owner": "mine", "state": "confirmed", "status": "active", "hand": 0}]
    out = build_table_presentation(_snapshot(groups=[_group(cards=[])], card_registry=registry), running=True)
    assert _hero(out)["cards"] == []


def test_advice_is_never_projected_without_verifiable_link():
    # A real seat payload may contain recommendations, but index/mine alone is not a link to the hero group.
    snap = _ready_individual(seats=[{"index": 0, "mine": True, "advice": "P", "book_action": "P", "optimal": "P"}],
                             reading={"captured_at": 1, "inferred_at": 1, "present": True, "empty": False,
                                      "seats": {}, "dealer_ok": True, "count_uncertain": 0, "count_pending": 0})
    out = build_table_presentation(snap, running=True)
    assert out["advice_available"] is False and _hero(out)["advice"] is None


def test_metadata_is_carried_through():
    out = build_table_presentation(_ready_individual(), running=True)
    assert (out["seq"], out["round_id"], out["shoe_id"]) == (4, 9, "shoe-2")
    assert out["source_profile"] == "table-profile" and out["source_resolution"] == [1280, 720]


def test_outputs_deep_copy_geometry_legacy_refs_and_source_resolution():
    legacy = {"state": "confirmed", "rank": "A", "hand": 0, "legacy_ref": {"seat_index": 0, "card_index": 0},
              "bbox_norm": {"x": .1, "y": .2, "w": .3, "h": .4, "cx": .25, "cy": .4}}
    snap = _snapshot(groups=[_group(cards=[legacy, {**legacy, "rank": "K", "legacy_ref": {"seat_index": 0, "card_index": 1}}])], dealer=[_card("d", "5", owner="dealer")])
    before = copy.deepcopy(snap)
    first = build_table_presentation(snap, running=True)
    second = build_table_presentation(snap, running=True)
    first["players"][0]["cards"][0]["bbox_norm"]["x"] = -1
    first["players"][0]["cards"][0]["legacy_ref"]["seat_index"] = 9
    first["source_resolution"][0] = -1
    assert snap == before
    assert second["players"][0]["cards"][0]["bbox_norm"]["x"] == .1
    assert second["players"][0]["cards"][0]["legacy_ref"]["seat_index"] == 0
    assert second["source_resolution"] == [1280, 720]


def test_result_is_deterministic_and_independent_between_calls():
    snap = _ready_individual()
    one = build_table_presentation(snap, running=True)
    two = build_table_presentation(snap, running=True)
    assert one == two
    one["players"][0]["cards"][0]["bbox_norm"]["x"] = -1
    assert two["players"][0]["cards"][0]["bbox_norm"]["x"] == .4


def test_projection_module_has_no_prohibited_imports():
    module = Path(__file__).parents[1] / "lib" / "logic" / "table_presentation.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    forbidden = ("models", "strategy", "strategyadvisor", "advice", "counter", "engine",
                 "detectionengine", "detection_engine", "vision", "tkinter", "cv2", "cardmemory", "card_memory")
    assert not [name for name in imports if any(part in name.lower() for part in forbidden)]
