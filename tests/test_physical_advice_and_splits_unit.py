"""Physical-group strategy contracts: no legacy seats, GUI, or real models."""

from copy import deepcopy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.logic import ev_engine
from lib.logic.engine import DetectionEngine
from lib.logic.strategy import StrategyAdvisor
from lib.logic.table_presentation import build_table_presentation


def _card(card_id, rank, *, owner="mine", hand=0, state="confirmed",
          reason=None, ambiguous_seat=False):
    return {"card_id": card_id, "rank": rank if state == "confirmed" and not reason else None,
            "suit": None, "state": state, "reason": reason, "owner": owner,
            "hand": hand, "ambiguous_seat": ambiguous_seat}


def _group(seat_id="J4", cards=None, *, hero=True, ambiguous=False, split=False):
    return {"seat_id": seat_id, "is_hero": hero, "is_ambiguous": ambiguous,
            "has_split": split, "cards": list(cards or [])}


def _snapshot(*, mode="individual", groups=None, dealer=None, count=None,
              reading=None, round_id=5, shoe_id="shoe-x", **extra):
    groups, dealer = list(groups or []), list(dealer or [])
    physical_cards = [card for group in groups for card in group.get("cards", [])]
    physical_cards.extend(dealer)
    registry = []
    for serial, card in enumerate(physical_cards, start=1):
        card_id = f"{shoe_id}:{round_id}:{serial}"
        card["card_id"] = card_id
        registry.append({"id": card_id, "card_id": card_id,
                         "round_id": round_id, "status": "active",
                         "state": card.get("state", "confirmed"),
                         "rank": card.get("rank"), "owner": card.get("owner"),
                         "hand": card.get("hand"),
                         "counted": card.get("state") == "confirmed"})
    return {
        "source_mode": mode, "round": round_id, "shoe_id": shoe_id,
        "card_groups": groups, "dealer_cards": dealer,
        "card_registry": registry,
        "count": {"true": 1.5, "per_rank": _per_rank(),
                  "count_uncertain": False, **(count or {})},
        "reading": {"count_pending": False, "count_uncertain": False,
                    **(reading or {})},
        **extra,
    }


def _per_rank(**seen):
    result = {rank: 0 for rank in
              ("Ace", "2", "3", "4", "5", "6", "7", "8", "9", "10")}
    result.update(seen)
    return result


def _confirmed_hand(hand=0, prefix="h", ranks=("10", "8")):
    return [_card(f"{prefix}{i}", rank, hand=hand) for i, rank in enumerate(ranks)]


class RecordingStrategy:
    def __init__(self):
        self.calls = []

    def advice(self, player_cards, dealer_card, post_split=False):
        self.calls.append((list(player_cards), dealer_card, post_split))
        value = "H" if player_cards[0].startswith("2") else "S"
        return value, {"H": "Hit", "S": "Stand"}[value], f"color-{value}"


def _engine(*, real_strategy=False):
    engine = DetectionEngine.__new__(DetectionEngine)
    engine.strategy = StrategyAdvisor() if real_strategy else RecordingStrategy()
    engine.counter = SimpleNamespace(deck_count=8)
    engine._log_ev_error = Mock()
    engine._insurance_advice = MethodType(DetectionEngine._insurance_advice, engine)
    return engine


def _physical(engine, snapshot):
    return DetectionEngine._physical_group_advice(engine, snapshot)


def _single_player_snapshot(**kwargs):
    return _snapshot(
        groups=[_group(cards=_confirmed_hand())],
        dealer=[_card("d1", "6", owner="dealer")], **kwargs)


def _hero_advice(physical):
    return physical["hero"]["hands"]


def test_engine_advice_uses_confirmed_physical_hero_and_dealer_only():
    engine = _engine()
    snap = _single_player_snapshot(seats=[{
        "index": 0, "mine": True, "cards": ["Ace", "Ace"], "advice": "Split"}])
    result = _physical(engine, snap)
    assert result["hero"]["identified"] and not result["hero"]["ambiguous"]
    assert _hero_advice(result)[0]["status"] == "ready"
    assert _hero_advice(result)[0]["card_ids"] == ["shoe-x:5:1", "shoe-x:5:2"]
    assert _hero_advice(result)[0]["advice"]["text"] == "Stand"
    assert engine.strategy.calls == [(["10", "8"], "6", False)]


@pytest.mark.parametrize("groups,dealer,reason", [
    ([_group(cards=[_card("h0", "10")])], [_card("d1", "6", owner="dealer")], "INCOMPLETE_HAND"),
    ([_group(cards=[_card("h0", "10", state="pending"), _card("h1", "8")])],
     [_card("d1", "6", owner="dealer")], "HERO_UNCERTAIN"),
    ([_group(cards=_confirmed_hand())], [_card("d1", None, owner="dealer", state="pending")],
     "DEALER_UNCERTAIN"),
    ([_group(cards=_confirmed_hand())], [], "WAITING_DEALER"),
    ([_group(cards=_confirmed_hand(), ambiguous=True)],
     [_card("d1", "6", owner="dealer")], "HERO_AMBIGUOUS"),
])
def test_incomplete_or_ambiguous_inputs_withdraw_advice(groups, dealer, reason):
    engine = _engine()
    result = _physical(engine, _snapshot(groups=groups, dealer=dealer))
    if reason == "HERO_AMBIGUOUS":
        assert result["hero"]["reason"] == reason
    else:
        assert result["hero"]["hands"]
        assert all(hand["status"] == "no_advice" and hand["reason"] == reason
                   for hand in result["hero"]["hands"])
    assert engine.strategy.calls == []


def test_absent_or_conflicting_hero_never_defaults_to_first_group():
    engine = _engine()
    foreign = _group("J1", _confirmed_hand(prefix="o"), hero=False)
    no_hero = _physical(engine, _snapshot(mode="multiplayer", groups=[foreign],
                                          dealer=[_card("d", "6", owner="dealer")]))
    assert not no_hero["hero"]["identified"]
    assert no_hero["hero"]["reason"] == "NO_HERO_GROUP"
    assert engine.strategy.calls == []

    conflict = _physical(engine, _snapshot(mode="multiplayer", groups=[
        _group("J4", _confirmed_hand(prefix="a")),
        _group("J5", _confirmed_hand(prefix="b")),
    ], dealer=[_card("d", "6", owner="dealer")]))
    assert conflict["hero"]["ambiguous"]
    assert conflict["hero"]["reason"] == "HERO_IDENTITY_CONFLICT"


def test_ambiguous_owner_card_within_hero_blocks_hero_advice():
    engine = _engine()
    hero = _group(cards=[_card("h0", "10", ambiguous_seat=True), _card("h1", "8")])
    result = _physical(engine, _snapshot(groups=[hero],
                                         dealer=[_card("d", "6", owner="dealer")]))
    assert result["hero"]["hands"][0]["reason"] == "HERO_UNCERTAIN"
    assert engine.strategy.calls == []


@pytest.mark.parametrize("foreign_group", [
    _group("J2", [_card("o0", None, owner="other", state="pending")], hero=False),
    _group("TMP_1", [_card("o0", "2", owner="other")], hero=False, ambiguous=True),
])
def test_multiplayer_foreign_uncertainty_sets_flag_but_preserves_hero_advice(foreign_group):
    engine = _engine()
    groups = [_group("J4", _confirmed_hand()), foreign_group]
    result = _physical(engine, _snapshot(mode="multiplayer", groups=groups,
                                         dealer=[_card("d", "6", owner="dealer")]))
    assert result["count_has_pending_uncertainty"] is True
    assert result["hero"]["hands"][0]["status"] == "ready"
    assert result["hero"]["hands"][0]["advice"]["text"] == "Stand"
    assert len(engine.strategy.calls) == 1


def test_foreign_group_is_never_mixed_into_hero_strategy_input():
    engine = _engine()
    groups = [_group("J8", _confirmed_hand(prefix="other"), hero=False),
              _group("J4", _confirmed_hand(prefix="mine"), hero=True)]
    _physical(engine, _snapshot(mode="multiplayer", groups=groups,
                                dealer=[_card("d", "6", owner="dealer")]))
    assert engine.strategy.calls == [(["10", "8"], "6", False)]


def test_split_hands_get_independent_advice_and_keep_card_identity():
    engine = _engine()
    cards = [_card("s0", "2", hand=0), _card("s1", "8", hand=0),
             _card("s2", "10", hand=1), _card("s3", "8", hand=1)]
    result = _physical(engine, _snapshot(groups=[_group(cards=cards, split=True)],
                                         dealer=[_card("d", "6", owner="dealer")]))
    hands = result["hero"]["hands"]
    assert [item["split_index"] for item in hands] == [0, 1]
    assert [item["card_ids"] for item in hands] == [
        ["shoe-x:5:1", "shoe-x:5:2"], ["shoe-x:5:3", "shoe-x:5:4"]]
    assert [item["advice"]["text"] for item in hands] == ["Hit", "Stand"]
    assert engine.strategy.calls == [(["2", "8"], "6", True),
                                     (["10", "8"], "6", True)]


def test_ambiguous_split_hand_does_not_withdraw_other_hand_advice():
    engine = _engine()
    cards = [_card("s0", "2", hand=0), _card("s1", None, hand=0, state="pending"),
             _card("s2", "10", hand=1), _card("s3", "8", hand=1)]
    result = _physical(engine, _snapshot(groups=[_group(cards=cards, split=True)],
                                         dealer=[_card("d", "6", owner="dealer")]))
    hands = result["hero"]["hands"]
    assert hands[0]["status"] == "no_advice" and hands[0]["reason"] == "HERO_UNCERTAIN"
    assert hands[1]["status"] == "ready" and hands[1]["advice"]["text"] == "Stand"
    assert engine.strategy.calls == [(["10", "8"], "6", True)]


def test_unassigned_split_card_gets_its_own_no_advice_slot():
    engine = _engine()
    cards = [_card("s0", "2", hand=0), _card("s1", "8", hand=0),
             _card("s2", "10", hand=1), _card("s3", "8", hand=1),
             _card("unknown-hand", None, hand=None, state="pending")]
    result = _physical(engine, _snapshot(groups=[_group(cards=cards, split=True)],
                                         dealer=[_card("d", "6", owner="dealer")]))
    hands = result["hero"]["hands"]
    assert [hand["hand"] for hand in hands] == [0, 1, None]
    assert [hand["status"] for hand in hands] == ["ready", "ready", "no_advice"]
    assert hands[2]["reason"] == "SPLIT_HAND_AMBIGUOUS"
    assert hands[2]["card_ids"] == ["shoe-x:5:5"]
    assert engine.strategy.calls == [(["2", "8"], "6", True),
                                     (["10", "8"], "6", True)]


@pytest.mark.parametrize("field,value", [("round_id", 4), ("shoe_id", "old-shoe")])
def test_advice_payload_is_bound_to_current_round_and_shoe(field, value):
    engine = _engine()
    snap = _single_player_snapshot()
    snap["round" if field == "round_id" else field] = value
    result = _physical(engine, snap)
    assert result[field] == value
    assert result["hero"]["status"] == "no_advice"
    assert result["hero"]["reason"] == "HERO_AMBIGUOUS"
    assert result["hero"]["hands"] == []


def test_index_advice_is_suppressed_while_any_count_uncertainty_is_pending():
    engine = _engine()
    snap = _single_player_snapshot(count={"count_uncertain": True})
    result = _physical(engine, snap)
    assert result["count_has_pending_uncertainty"] is True
    assert result["hero"]["hands"][0]["status"] == "ready"
    assert result["hero"]["hands"][0]["advice"]["index_advice"] in (None, "")


def test_manual_rank_change_in_physical_snapshot_recomputes_basic_advice():
    engine = _engine(real_strategy=True)
    cards = [_card("h0", "10"), _card("h1", "6")]
    snap = _snapshot(groups=[_group(cards=cards)],
                     dealer=[_card("d", "10", owner="dealer")])
    before = _physical(engine, snap)["hero"]["hands"][0]["advice"]
    assert before["action"] == "R/H"
    cards[1]["rank"] = "8"  # corrected physical rank in the next publication
    snap["card_registry"][1]["rank"] = "8"
    after = _physical(engine, snap)["hero"]["hands"][0]["advice"]
    assert after["action"] == "S"


def test_manual_dealer_upcard_change_recomputes_hero_advice():
    engine = _engine(real_strategy=True)
    cards = [_card("h0", "10"), _card("h1", "6")]
    dealer = [_card("d", "10", owner="dealer")]
    snap = _snapshot(groups=[_group(cards=cards)], dealer=dealer)
    assert _physical(engine, snap)["hero"]["hands"][0]["advice"]["action"] == "R/H"
    dealer[0]["rank"] = "6"
    snap["card_registry"][2]["rank"] = "6"
    assert _physical(engine, snap)["hero"]["hands"][0]["advice"]["action"] == "S"


def test_insurance_uses_physical_confirmed_ace_and_existing_hilo_ev():
    engine = _engine(real_strategy=True)
    seen = _per_rank(**{rank: 10 for rank in
                        ("Ace", "2", "3", "4", "5", "6", "7", "8", "9")})
    snap = _snapshot(groups=[_group(cards=_confirmed_hand())],
                     dealer=[_card("d", "Ace", owner="dealer")],
                     count={"per_rank": seen})
    result = _physical(engine, snap)
    assert result["insurance"]["status"] == "ready"
    assert result["insurance"]["take"] is True
    assert result["insurance"]["ev"] == pytest.approx(
        ev_engine.insurance_advice(seen, deck_count=8)["ev"])


@pytest.mark.parametrize("dealer,expected", [
    ([_card("d", "Ace", owner="dealer", state="pending")], None),
    ([_card("d", "6", owner="dealer")], None),
])
def test_insurance_requires_confirmed_physical_dealer_ace(dealer, expected):
    result = _physical(_engine(), _snapshot(groups=[_group(cards=_confirmed_hand())],
                                            dealer=dealer))
    assert result["insurance"] is expected


def _physical_payload(*, round_id=5, shoe_id="shoe-x", hands=None, **extra):
    advice_hands = hands or [{
        "hand": None, "hand_id": "hero", "split_index": None,
        "card_ids": ["h0", "h1"], "status": "ready", "reason": None,
        "advice": {"action": "S", "text": "Stand", "color": "green",
                   "index_advice": "Index: Hit"},
    }]
    return {
        "round_id": round_id, "shoe_id": shoe_id,
        "count_has_pending_uncertainty": False,
        "hero": {"identified": True, "ambiguous": False, "group_id": "J4",
                 "card_ids": ["h0", "h1"], "hands": advice_hands},
        "insurance": {"status": "ready", "take": False,
                      "text": "INSURANCE: NO", "ev": -.02},
        **extra,
    }


def _presentation_snapshot(*, physical=None, groups=None, dealer=None,
                           round_id=5, shoe_id="shoe-x", **extra):
    return {
        "source_mode": "individual", "source_profile": "P",
        "source_resolution": [1280, 720], "seq": 3, "round": round_id,
        "shoe_id": shoe_id, "card_groups": groups or [
            _group(cards=[_card("h0", "10"), _card("h1", "8")])],
        "dealer_cards": dealer or [_card("d", "6", owner="dealer")],
        "reading": {}, "count": {}, "physical_advice": physical or _physical_payload(),
        **extra,
    }


def test_table_presentation_attaches_advice_only_to_exact_physical_hero_cards():
    snap = _presentation_snapshot(seats=[{"mine": True, "advice": "OLD SPLIT"}])
    before = deepcopy(snap)
    result = build_table_presentation(snap, running=True)
    hero = result["players"][0]
    assert result["advice_available"] is True
    assert hero["hands"][0]["advice"]["text"] == "Stand"
    assert hero["advice"].startswith("Stand")
    assert result["insurance"]["text"] == "INSURANCE: NO"
    assert result["count_has_pending_uncertainty"] is False
    assert snap == before


@pytest.mark.parametrize("field,value", [("round_id", 4), ("shoe_id", "old-shoe")])
def test_presentation_discards_stale_round_or_shoe_advice(field, value):
    payload = _physical_payload(**{field: value})
    result = build_table_presentation(_presentation_snapshot(physical=payload), running=True)
    assert result["advice_available"] is False
    assert "hands" not in result["players"][0]
    assert result["players"][0]["advice"] is None
    assert result["insurance"] is None


def test_physical_advice_does_not_read_historical_seats():
    snap = _presentation_snapshot(seats=[{"index": 0, "mine": True,
                                          "advice": "WRONG", "optimal": "WRONG"}])
    result = build_table_presentation(snap, running=True)
    assert result["players"][0]["advice"].startswith("Stand")
    assert "WRONG" not in str(result["players"][0])


def test_multiplayer_unrelated_pending_group_keeps_hero_advice_and_flag():
    groups = [_group("J4", [_card("h0", "10"), _card("h1", "8")]),
              _group("TMP_1", [_card("o", None, owner="other", state="pending")],
                     hero=False, ambiguous=True)]
    payload = _physical_payload()
    payload["count_has_pending_uncertainty"] = True
    snap = _presentation_snapshot(physical=payload, groups=groups)
    snap["source_mode"] = "multiplayer"
    result = build_table_presentation(snap, running=True)
    assert result["status"] == "uncertain"
    assert result["advice_available"] is True
    assert result["count_has_pending_uncertainty"] is True
    assert result["players"][0]["advice"].startswith("Stand")


def test_uncertain_global_status_does_not_hide_valid_physical_hero_advice():
    from lib.interfaces.round_overlay import MiniTableOverlay

    p = build_table_presentation(_presentation_snapshot(
        physical={**_physical_payload(), "count_has_pending_uncertainty": True},
        count={"count_uncertain": True}), running=True)
    assert p["status"] == "uncertain" and p["advice_available"] is True
    assert "Stand" in MiniTableOverlay._advice_text(p, running=True)


def test_split_presenter_keeps_one_player_and_hand_local_advice():
    cards = [_card("s0", "2", hand=0), _card("s1", "8", hand=0),
             _card("s2", "10", hand=1), _card("s3", "8", hand=1)]
    hands = [
        {"hand": 0, "hand_id": "hero:0", "split_index": 0,
         "card_ids": ["s0", "s1"], "status": "no_advice",
         "reason": "HERO_UNCERTAIN", "advice": None},
        {"hand": 1, "hand_id": "hero:1", "split_index": 1,
         "card_ids": ["s2", "s3"], "status": "ready", "reason": None,
         "advice": {"action": "S", "text": "Stand", "color": "green",
                    "index_advice": ""}},
    ]
    payload = _physical_payload(hands=hands)
    payload["hero"]["card_ids"] = ["s0", "s1", "s2", "s3"]
    groups = [_group("J4", cards, split=True)]
    result = build_table_presentation(_presentation_snapshot(
        physical=payload, groups=groups), running=True)
    assert len(result["players"]) == 1
    hero = result["players"][0]
    assert len(hero["hands"]) == 2
    assert hero["hands"][0]["advice"] is None
    assert hero["hands"][1]["advice"]["text"] == "Stand"
    assert [c["card_id"] for c in hero["cards"]] == ["s0", "s1", "s2", "s3"]


@pytest.mark.parametrize("round_id,shoe_id", [(6, "shoe-x"), (5, "shoe-next")])
def test_new_round_or_shoe_cannot_reuse_previous_presentation_advice(round_id, shoe_id):
    physical = _physical_payload()
    snap = _presentation_snapshot(physical=physical, round_id=round_id, shoe_id=shoe_id)
    result = build_table_presentation(snap, running=True)
    assert result["advice_available"] is False
    assert result["players"][0]["advice"] is None
    assert result["insurance"] is None


def test_split_post_split_flag_preserves_strategy_rules():
    strategy = StrategyAdvisor()
    cards = ["8 of Hearts", "8 of Spades"]
    normal, _, _ = strategy.advice(cards, "King")
    post_split, _, _ = strategy.advice(cards, "King", post_split=True)
    assert normal == "P"
    assert post_split != "P"


def test_views_consume_same_presented_split_advice_without_recalculating(monkeypatch):
    from tests.test_individual_table_view_unit import view as table_view_fixture
    from tests.test_mini_table_presentation_unit import mini as mini_fixture

    p = build_table_presentation(_presentation_snapshot(), running=True)
    assert p["players"][0]["advice"].startswith("Stand")
    # The widgets consume the identical detached presentation. The shared view
    # fixtures use fake widgets/canvas only; strategy is absent from both.
    table = table_view_fixture.__wrapped__(monkeypatch)
    mini = mini_fixture.__wrapped__(monkeypatch)
    table.update(p)
    mini.update_from_presentation(p)
    assert "Stand" in table.seats[0]["advice"].cget("text")
    assert any("Stand" in text for text in mini.canvas.texts())
    assert table.seats[0]["data"]["hands"] == mini._last_state["players"][0]["hands"]


def test_corrected_physical_dealer_rank_refreshes_both_views_from_new_presentation(monkeypatch):
    from tests.test_individual_table_view_unit import view as table_view_fixture
    from tests.test_mini_table_presentation_unit import mini as mini_fixture

    engine = _engine(real_strategy=True)
    snap = _snapshot(groups=[_group(cards=_confirmed_hand(ranks=("10", "6")))],
                     dealer=[_card("d1", "10", owner="dealer")])
    table = table_view_fixture.__wrapped__(monkeypatch)
    mini = mini_fixture.__wrapped__(monkeypatch)

    def update_views():
        snap["physical_advice"] = _physical(engine, snap)
        presentation = build_table_presentation(snap, running=True)
        table.update(presentation)
        mini.update_from_presentation(presentation)
        return presentation

    before = update_views()
    assert before["players"][0]["hands"][0]["advice"]["action"] == "R/H"
    assert "Surrender / Hit" in table.seats[0]["advice"].cget("text")
    assert any("Surrender / Hit" in text for text in mini.canvas.texts())

    dealer = snap["dealer_cards"][0]
    dealer["rank"] = "6"
    registry_card = next(row for row in snap["card_registry"]
                         if row["id"] == dealer["card_id"])
    registry_card["rank"] = "6"
    after = update_views()
    assert after["players"][0]["hands"][0]["advice"]["action"] == "S"
    assert "Stand" in table.seats[0]["advice"].cget("text")
    assert any("Stand" in text for text in mini.canvas.texts())
