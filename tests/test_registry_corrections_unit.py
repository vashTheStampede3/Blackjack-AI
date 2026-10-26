"""Identity/correction invariants with real ledgers and isolated engine methods.

No engine constructor, capture, GUI, model, persistence worker or strategy is run.
Snapshot tests use the production publisher with unrelated advice seams stubbed.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from threading import Lock, RLock
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.logic import engine as engine_module
from lib.logic.card_layout import CardLayout
from lib.logic.card_memory import CardMemory
from lib.logic.counting import CardCounter
from lib.logic.engine import DetectionEngine, Seat
from lib.logic.round_observation import RoundObservation
from lib.logic.table_presentation import build_table_presentation


def observation(rank="2", x=50., owner="mine", suit=None):
    return dict(name=rank, cx=x, cy=120. if owner != "dealer" else 30.,
                width=20., height=30., owner=owner, suit=suit)


def ledger(*observations, confirmations=1):
    memory = CardMemory(CardCounter(), confirmations=confirmations)
    memory.observe(list(observations or [observation()]), 1.)
    return memory


def identity(memory):
    return next(iter(memory.cards))


def state(memory):
    return deepcopy((memory.counter.get_state(), memory.get_state(),
                     [asdict(g) for g in memory.group_tracker.ordered_groups()]))


def layout(mode="individual", resolution=(320, 180)):
    data = {"mode": mode, "resolution": list(resolution),
            "mine": [[20, 100], [100, 100], [100, 160], [20, 160]],
            "dealer": [[100, 20], [200, 20], [200, 60], [100, 60]]}
    if mode == "multiplayer":
        data["table"] = [[5, 85], [315, 85], [315, 175], [5, 175]]
    return CardLayout.from_payload(data, resolution)


@pytest.fixture
def engine(monkeypatch):
    # Dependencies that read configuration or compute unrelated advice are
    # isolated; counting, memory, grouping and snapshot publication remain real.
    monkeypatch.setattr(engine_module.region_profiles, "get_card_layout",
                        lambda resolution, mode, **kw: layout(mode, resolution))
    monkeypatch.setattr(engine_module.region_profiles, "get_source_context", lambda *a, **kw: None)
    monkeypatch.setattr(engine_module.ocr, "load_regions", lambda *a: None)
    monkeypatch.setattr(engine_module.betting, "suggest", lambda *a, **kw: dict(
        capped=False, text="", bet=0, sit_out=False, edge=0))
    e = SimpleNamespace(
        _lock=RLock(), _preview_cache_lock=RLock(), _snapshot_lock=Lock(), _advice_lock=Lock(),
        _model_metadata_lock=Lock(), _model_metadata_cache={}, _metadata_model_refs={},
        capture=SimpleNamespace(resolution=(320, 180), monitor=None),
        counter=CardCounter(), card_registry=None, card_layout=None, round_number=1,
        _active_mode=None, _active_profile=None, _registry_serial=0,
        _round_observation=RoundObservation(), _unread_positions={}, _pending_extra={},
        _pending_positions={}, _prev_seat_thumbs={}, _prev_thumb=None, _prev_dealer_thumb=None,
        _preview_detection_cache=None, _last_frame=None, _source_mismatch=None,
        regions=[], seats=[Seat(i) for i in range(7)], my_seats={0},
        dealer_card=None, dealer_extras=[], dealer_locked=False, _dealer_counted=False,
        _dealer_history=deque(), _dealer_miss_streak=0, _pending_dealer={}, _dealer_pos=None,
        _pending_cutting_card={}, cutting_card_seen=False, _reshuffle_dismissed=False,
        phase_detector=SimpleNamespace(configure=Mock()), _phase_state={}, discipline={},
        _anchor_state={"status": "none", "fit": None, "drift": False, "calib_res": None},
        _metrics={}, provider=SimpleNamespace(backend_name="unit"), last_error=None,
        _model_refresh_ts=None, _seq=0, _snapshot=None, _last_activity="waiting",
        seat_play={i: {"n": 0, "book": 0} for i in range(7)}, session_pnl={},
        bet_placed=1., bet_capped_rounds=0, _bet_was_capped=False, _predeal={"edge": None},
        _ocr_regions=None, _ocr_last={}, paytable_changed_midshoe=False,
        _ev_error_keys=set(), _pending_discipline=[], _my_turn_baseline={},
        _settings_dirty=False, _round_stakes=None, store=None, log=Mock(),
        strategy=SimpleNamespace(advice=Mock(return_value=(None, "", "#000000"))),
        _optimal_advice=Mock(return_value=("", "#000000", None)),
        _index_advice=Mock(return_value=("", "#000000")),
        _ev_count=lambda count: count, _insurance_advice=lambda *a: None,
        _side_bet_evs=lambda *a: [], _predeal_edge=lambda *a: None,
        _bet_behind_text=lambda *a: "", _guardrail_block=lambda: {}, _settle_round=Mock(return_value={}))

    def set_source(source):
        e.capture.monitor = source
        e.capture.resolution = (source.width, source.height)

    e.capture.set_monitor = set_source
    for name in ("set_monitor", "_sync_registry_cards", "publish_snapshot", "get_snapshot",
                 "apply_correction", "correct_tracked_card", "new_round", "_reset_round_state",
                 "reset_shoe", "_shoe_state", "apply_shoe_state", "use_card_layout"):
        setattr(e, name, MethodType(getattr(DetectionEngine, name), e))
    e._advice_withdrawal_reason = DetectionEngine._advice_withdrawal_reason
    e.set_monitor(SimpleNamespace(width=320, height=180, x=0, y=0), profile="A", mode="individual")
    return e


def observe(engine, observations, now=1.):
    with engine._lock:
        for tick in range(engine.card_registry.confirmations):
            engine.card_registry.observe(observations, now + tick)
        engine._sync_registry_cards()
        engine.publish_snapshot()


@pytest.mark.parametrize("change", ["profile", "mode", "source", "resolution"])
def test_configuration_replacement_never_recycles_an_old_identity(engine, change):
    observe(engine, [observation()])
    old_memory, old_id = engine.card_registry, identity(engine.card_registry)
    source = SimpleNamespace(width=320, height=180, x=0, y=0)
    if change == "source":
        source.x = 320
    elif change == "resolution":
        source.width, source.height = 640, 360
    engine.set_monitor(source, profile="B" if change == "profile" else "A",
                       mode="multiplayer" if change == "mode" else "individual")
    observe(engine, [observation("10")], 10.)
    new_id = identity(engine.card_registry)
    assert old_id != new_id and engine.card_registry is not old_memory
    assert old_id.rsplit(":", 2)[:2] == new_id.rsplit(":", 2)[:2]
    assert int(new_id.rsplit(":", 1)[1]) > int(old_id.rsplit(":", 1)[1])
    before, sequence = state(engine.card_registry), engine.get_snapshot()["seq"]
    result = engine.apply_correction(old_id, engine.round_number, "set_rank", new_rank="6")
    assert not result.ok and result.rc_delta == 0
    assert state(engine.card_registry) == before and engine.get_snapshot()["seq"] == sequence
    assert engine.card_registry.cards[new_id].rank == "10"


def test_identical_source_revalidation_preserves_memory_and_ids(engine):
    observe(engine, [observation()])
    memory, before, seq = engine.card_registry, state(engine.card_registry), engine.get_snapshot()["seq"]
    engine.set_monitor(SimpleNamespace(width=320, height=180, x=0, y=0), profile="A", mode="individual")
    assert engine.card_registry is memory and state(memory) == before
    assert engine.get_snapshot()["seq"] == seq


def test_equal_ranks_and_round_changes_preserve_distinct_identities(engine):
    observe(engine, [observation("8", 40), observation("8", 80)])
    old = set(engine.card_registry.cards)
    counter = engine.counter.get_state()
    engine.new_round()
    assert engine.counter.get_state() == counter
    assert engine.round_number == engine.card_registry.round_number == 2
    observe(engine, [observation("8", 40)], 10.)
    assert old.isdisjoint(engine.card_registry.cards)
    rejected = engine.apply_correction(next(iter(old)), 1, "set_rank", new_rank="5")
    assert not rejected.ok and rejected.reason == "round_changed"


def test_plausible_small_motion_keeps_physical_identity_and_count():
    m = ledger(observation("8"))
    old = identity(m)
    for tick in range(2, 7):
        m.observe([observation("8", 50. + tick * .5)], tick)
    assert set(m.cards) == {old} and m.counter.cards_seen == 1


def test_restore_reserves_ids_even_if_serial_is_missing_and_does_not_recount():
    m = ledger(observation("8"))
    saved = m.get_state()
    saved.pop("serial")
    saved_before, counter_before = deepcopy(saved), m.counter.get_state()
    restored = CardMemory(m.counter, confirmations=1)
    restored.apply_state(saved)
    assert restored.counter.get_state() == counter_before and saved == saved_before
    restored.observe([observation("8"), observation("9", 220)], 2.)
    assert len(restored.cards) == 2 and len(set(restored.cards)) == 2
    assert restored.counter.cards_seen == 2
    restored.cards[identity(restored)].trajectory.append((999, 0, 0))
    assert saved == saved_before


def test_restore_older_state_does_not_lower_the_high_water_mark(engine):
    observe(engine, [observation()])
    saved = engine._shoe_state()
    engine.set_monitor(SimpleNamespace(width=320, height=180, x=320, y=0), profile="A", mode="individual")
    observe(engine, [observation("9")], 10.)
    omitted_id = identity(engine.card_registry)
    engine.apply_shoe_state({"state": saved, "round_number": 1})
    restored_ids = set(engine.card_registry.cards)
    observe(engine, [observation(), observation("8", 220)], 20.)
    assert omitted_id not in engine.card_registry.cards
    assert restored_ids <= set(engine.card_registry.cards)
    assert engine.counter.cards_seen == 2


def test_persisted_shoe_doubts_reserve_ids_without_becoming_active_cards():
    m = ledger(observation(None))
    old = identity(m)
    m.clear_round()
    fresh = CardMemory(m.counter, confirmations=1)
    fresh.observe([observation("8")], 10.)
    assert identity(fresh) != old
    assert old in fresh.counter.uncertainties
    before = state(fresh)
    assert not fresh.apply_correction(old, 1, "set_rank", new_rank="6").ok
    assert state(fresh) == before


@pytest.mark.parametrize("invalid", ["duplicate", "other_shoe", "wrong_round", "invalid_id", "negative_serial", "null_id"])
def test_invalid_restoration_is_rejected_before_replacing_memory(invalid):
    m = ledger()
    saved = m.get_state()
    if invalid == "duplicate":
        saved["cards"].append(deepcopy(saved["cards"][0]))
    elif invalid == "other_shoe":
        saved["cards"][0]["id"] = "other:1:9"
    elif invalid == "wrong_round":
        saved["cards"][0]["round_id"] = 2
    elif invalid == "invalid_id":
        saved["cards"][0]["id"] = m.counter.shoe_id + ":1:x"
    elif invalid == "null_id":
        saved["cards"][0]["id"] = None
    else:
        saved["serial"] = -1
    before = state(m)
    with pytest.raises(ValueError):
        m.apply_state(saved)
    assert state(m) == before


@pytest.mark.parametrize("old,new,delta", [("2", "10", -2), ("10", "2", 2), ("8", "8", 0)])
def test_rank_correction_changes_one_contribution_and_reports_actual_delta(old, new, delta):
    m = ledger(observation(old))
    cid = identity(m)
    before = m.counter.running_count
    result = m.apply_correction(cid, 1, "set_rank", new_rank=new)
    assert result.ok and result.rc_delta == delta == m.counter.running_count - before
    assert m.counter.cards_seen == 1 and sum(m.counter.per_rank.values()) == 1
    assert m.counter.per_rank[new] == 1
    if new != old:
        assert m.counter.per_rank[old] == 0
    assert m.cards[cid].rank == new and m.cards[cid].counted_name == new
    for tick in range(2, 6):
        m.observe([observation(old)], tick)
    assert m.counter.cards_seen == 1 and m.cards[cid].rank == new


@pytest.mark.parametrize("owner", ["mine", "dealer", "other"])
def test_pending_manual_confirmation_counts_once_and_keeps_unrelated_doubt(owner):
    m = ledger(observation(None, owner=owner))
    cid = identity(m)
    m.counter.mark_uncertainty("unrelated", {"reason": "keep"})
    result = m.apply_correction(cid, 1, "set_rank", new_rank="A", new_suit="S")
    assert result.ok and result.rc_delta == -1
    c = m.cards[cid]
    assert c.rank == "Ace" and c.suit == "S" and c.manual and c.counted
    assert c.counted_name == "Ace of Spades" and c.state == "confirmed"
    assert m.counter.cards_seen == 1 and m.counter.suit_seen["Ace of Spades"] == 1
    assert set(m.counter.uncertainties) == {"unrelated"}
    again = m.apply_correction(cid, 1, "set_rank", new_rank="Ace", new_suit="S")
    assert not again.ok and again.rc_delta == 0 and m.counter.cards_seen == 1


@pytest.mark.parametrize("old_suit,new_suit", [(None, "H"), ("S", "H"), ("D", "♥"), (None, "Hearts")])
def test_suit_only_update_moves_suit_totals_without_changing_hilo_or_seen(old_suit, new_suit):
    m = ledger(observation("2", suit=old_suit))
    result = m.apply_correction(identity(m), 1, "set_rank", new_rank="2", new_suit=new_suit)
    assert result.ok and result.rc_delta == 0
    assert m.counter.running_count == 1 and m.counter.cards_seen == 1
    assert m.counter.per_rank["2"] == 1 and m.counter.suit_seen["2 of Hearts"] == 1
    assert sum(m.counter.suit_seen.values()) == 1
    assert sum(m.counter.rank_seen_nosuit.values()) == 0
    assert m.cards[identity(m)].suit == "H" and m.cards[identity(m)].counted_name == "2 of Hearts"


def test_rank_and_suit_change_updates_both_composition_dimensions():
    m = ledger(observation("2", suit="S"))
    result = m.apply_correction(identity(m), 1, "set_rank", new_rank="King of Hearts")
    assert result.ok and result.rc_delta == -2
    assert m.counter.running_count == -1 and m.counter.cards_seen == 1
    assert m.counter.per_rank["2"] == 0 and m.counter.per_rank["10"] == 1
    assert m.counter.suit_seen["2 of Spades"] == 0 and m.counter.suit_seen["King of Hearts"] == 1


def test_rank_only_correction_does_not_invent_a_suit():
    m = ledger(observation("2"))
    assert m.apply_correction(identity(m), 1, "set_rank", new_rank="King").ok
    assert m.cards[identity(m)].suit is None
    assert m.counter.suit_seen == {} and m.counter.rank_seen_nosuit["King"] == 1


def test_same_rank_confirmation_does_not_retract_or_recount(monkeypatch):
    m = ledger()
    count, uncount = Mock(wraps=m.counter.count_card), Mock(wraps=m.counter.uncount_card)
    monkeypatch.setattr(m.counter, "count_card", count)
    monkeypatch.setattr(m.counter, "uncount_card", uncount)
    assert m.apply_correction(identity(m), 1, "set_rank", new_rank="2").ok
    count.assert_not_called()
    uncount.assert_not_called()
    before = state(m)
    assert not m.apply_correction(identity(m), 1, "set_rank", new_rank="2").ok
    assert state(m) == before


@pytest.mark.parametrize("kwargs", [
    {"new_rank": "bad"}, {"new_rank": ""}, {"new_rank": "-"}, {"new_rank": []},
    {"new_rank": "2 garbage"}, {"new_rank": "2", "new_suit": "X"},
    {"new_rank": "2", "new_suit": []}, {"new_rank": "2 of Dragons"},
    {"new_rank": "2 of Hearts", "new_suit": "S"}, {"new_rank": "2", "new_seat_id": "J1"},
])
def test_invalid_values_are_logically_atomic(kwargs):
    m = ledger(observation("2", suit="D"))
    m.counter.mark_uncertainty(identity(m), {"reason": "unresolved"})
    before = state(m)
    result = m.apply_correction(identity(m), 1, "set_rank", **kwargs)
    assert not result.ok and result.rc_delta == 0 and state(m) == before


@pytest.mark.parametrize("case", ["unknown", "wrong_round", "wrong_shoe", "invalid_id", "retired", "bad_action", "invalid_seat", "bad_round_type"])
def test_invalid_identity_or_action_never_changes_memory_groups_or_counter(case):
    m = ledger()
    cid, rnd, action, args = identity(m), 1, "set_rank", {"new_rank": "10"}
    if case == "unknown":
        cid = m.counter.shoe_id + ":1:999"
    elif case == "wrong_round":
        rnd = 2
    elif case == "wrong_shoe":
        cid = "old:1:1"
    elif case == "invalid_id":
        cid = None
    elif case == "retired":
        m.detach_shoe()
    elif case == "bad_action":
        action = "unsupported"
    elif case == "invalid_seat":
        action, args = "change_seat", {"new_seat_id": "hero"}
    else:
        rnd = True
    before = state(m)
    result = m.apply_correction(cid, rnd, action, **args)
    assert not result.ok and result.rc_delta == 0 and state(m) == before


@pytest.mark.parametrize("owner", ["mine", "dealer", "other"])
@pytest.mark.parametrize("reading", ["confirmed", "pending", "doubtful"])
def test_discard_retracts_once_removes_active_card_and_resolves_only_its_doubt(owner, reading):
    m = ledger(observation(None if reading == "pending" else "2", owner=owner))
    cid = identity(m)
    if reading == "doubtful":
        m._issue(m.cards[cid], "rank conflict")
    m.counter.mark_uncertainty("unrelated", {"reason": "keep"})
    before = m.counter.running_count
    result = m.apply_correction(cid, 1, "not_a_card")
    assert result.ok and result.rc_delta == -before
    assert m.counter.cards_seen == m.counter.running_count == 0
    assert m.cards[cid].state == "rejected" and m.cards[cid].status == "not_a_card"
    assert not any(c.id == cid for g in m.group_tracker.ordered_groups() for c in g.cards)
    assert set(m.counter.uncertainties) == {"unrelated"}
    saved = state(m)
    again = m.apply_correction(cid, 1, "not_a_card")
    assert not again.ok and again.rc_delta == 0 and state(m) == saved
    assert not m.apply_correction(cid, 1, "set_rank", new_rank="10").ok
    for tick in range(2, 5):
        m.observe([observation("2", owner=owner)], tick)
    assert m.counter.cards_seen == 0 and set(m.cards) == {cid}


def test_correction_of_doubtful_card_preserves_group_and_other_card_ids():
    m = ledger(observation("2", 40), observation("9", 80))
    ids = set(m.cards)
    cid = identity(m)
    group = next(g.seat_id for g in m.group_tracker.ordered_groups() if g.is_hero)
    m._issue(m.cards[cid], "conflict")
    assert m.apply_correction(cid, 1, "set_rank", new_rank="10").ok
    assert set(m.cards) == ids
    groups = [g for g in m.group_tracker.ordered_groups() if g.cards]
    assert len(groups) == 1 and groups[0].seat_id == group
    assert not m.counter.count_uncertain


@pytest.mark.parametrize("action", ["set_rank", "not_a_card"])
def test_real_snapshot_is_coherent_and_publishing_does_not_count_again(engine, action):
    observe(engine, [observation("2"), observation("7", 140, "dealer")])
    cid = next(c.id for c in engine.card_registry.cards.values() if c.owner == "mine")
    previous = engine.get_snapshot()
    result = engine.apply_correction(cid, 1, action, **({"new_rank": "10", "new_suit": "H"} if action == "set_rank" else {}))
    current = engine.get_snapshot()
    assert result.ok and current["seq"] == previous["seq"] + 1
    assert current["round"] == previous["round"] == 1
    assert current["shoe_id"] == previous["shoe_id"] == engine.counter.shoe_id
    assert current["source_profile"] == "A" and current["source_mode"] == "individual"
    assert current["count"] == engine.counter.snapshot()
    assert not current["reading"]["count_pending"] and not current["reading"]["count_uncertain"]
    projected = [c for g in current["card_groups"] for c in g["cards"]]
    if action == "set_rank":
        assert [(c["card_id"], c["rank"], c["suit"]) for c in projected] == [(cid, "10", "H")]
    else:
        assert projected == [] and current["card_groups"] == []
        p = build_table_presentation(current, selected_mode="individual", running=True)
        assert p["players"][0]["cards"] == []
    before = engine.counter.get_state()
    for _ in range(3):
        engine.publish_snapshot()
    assert engine.counter.get_state() == before
    assert set(engine.card_registry.cards) == {cid, current["dealer_cards"][0]["card_id"]}


def test_discarded_dealer_is_absent_from_snapshot_and_does_not_block_other_dealer(engine):
    observe(engine, [observation("2", 110, "dealer"), observation("7", 180, "dealer")])
    cid = next(c.id for c in engine.card_registry.cards.values() if c.rank == "2")
    assert engine.apply_correction(cid, 1, "not_a_card").ok
    snap = engine.get_snapshot()
    assert [c["rank"] for c in snap["dealer_cards"]] == ["7"]
    assert snap["reading"]["dealer_ok"] and not snap["count"]["count_uncertain"]


def test_rejected_or_unchanged_correction_keeps_snapshot_and_sequence(engine):
    observe(engine, [observation()])
    cid = identity(engine.card_registry)
    assert engine.apply_correction(cid, 1, "set_rank", new_rank="2").ok
    for kwargs in ({"new_rank": "2"}, {"new_rank": "bad"}):
        before, snap = state(engine.card_registry), deepcopy(engine.get_snapshot())
        result = engine.apply_correction(cid, 1, "set_rank", **kwargs)
        assert not result.ok and result.rc_delta == 0
        assert engine.get_snapshot() == snap and state(engine.card_registry) == before


def test_new_shoe_invalidates_ids_and_keeps_engine_memory_rounds_in_sync(engine):
    observe(engine, [observation(), observation("7", 140, "dealer")])
    cid, shoe = identity(engine.card_registry), engine.counter.shoe_id
    engine.reset_shoe()
    assert engine.counter.shoe_id != shoe
    assert engine.round_number == engine.card_registry.round_number == 2
    before = state(engine.card_registry)
    result = engine.apply_correction(cid, 2, "set_rank", new_rank="10")
    assert not result.ok and result.reason == "shoe_changed" and state(engine.card_registry) == before
    assert engine.get_snapshot()["dealer_cards"] == engine.get_snapshot()["card_groups"] == []
    assert "shoe_reset_with_cards" in engine.counter.uncertainties
    engine.new_round()
    assert engine.round_number == engine.card_registry.round_number == 3
    observe(engine, [observation("10")], 10.)
    assert identity(engine.card_registry) != cid
    assert engine.apply_correction(identity(engine.card_registry), 3, "set_rank", new_rank="2").ok
    assert "shoe_reset_with_cards" in engine.counter.uncertainties


@pytest.mark.parametrize("action", ["set_rank", "not_a_card"])
def test_concurrent_duplicate_corrections_have_one_accounting_effect_and_one_publication(engine, action):
    observe(engine, [observation()])
    cid, seq = identity(engine.card_registry), engine.get_snapshot()["seq"]
    kwargs = {"new_rank": "10"} if action == "set_rank" else {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: engine.apply_correction(cid, 1, action, **kwargs), range(12)))
    assert sum(result.ok for result in results) == 1
    assert engine.get_snapshot()["seq"] == seq + 1
    assert engine.counter.cards_seen == (1 if action == "set_rank" else 0)
    assert engine.counter.running_count == (-1 if action == "set_rank" else 0)
    assert sum(result.rc_delta for result in results) == (-2 if action == "set_rank" else -1)


def test_fresh_counter_and_memory_restore_ids_composition_and_serial_without_recount():
    m = ledger(observation("2", suit="D"), observation("7", 140, "dealer"))
    saved_counter, saved_memory = m.counter.get_state(), m.get_state()
    restored_counter = CardCounter()
    restored_counter.apply_state(saved_counter)
    restored = CardMemory(restored_counter, confirmations=1)
    restored.apply_state(saved_memory)
    assert set(restored.cards) == set(m.cards)
    assert restored_counter.get_state() == saved_counter
    restored.observe([observation("2", suit="D"), observation("7", 140, "dealer"),
                      observation("9", 220)], 2.)
    assert len(restored.cards) == 3 and restored_counter.cards_seen == 3
    assert restored_counter.suit_seen["2 of Diamonds"] == 1
    assert set(m.cards) < set(restored.cards)


def test_temporary_legacy_configuration_does_not_erase_registry_serial(engine):
    observe(engine, [observation(None)])
    old = identity(engine.card_registry)
    engine._seat_for_point = MethodType(DetectionEngine._seat_for_point, engine)
    engine.use_card_layout(layout(), memory="legacy")
    assert engine.card_registry is None
    engine.use_card_layout(layout(), memory="registry")
    observe(engine, [observation("8")], 10.)
    assert identity(engine.card_registry) != old
    assert old in engine.counter.uncertainties


def test_corrections_do_not_access_widgets_or_models(engine, monkeypatch):
    import builtins
    observe(engine, [observation()])
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"tkinter", "cv2", "ultralytics"}:
            raise AssertionError(f"Unexpected runtime access: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert engine.correct_tracked_card(identity(engine.card_registry), "King", expected_round=1).ok
    assert engine.counter.cards_seen == 1 and engine.counter.running_count == -1


def test_reobserving_a_discarded_detection_cannot_restore_present_flag_or_visual_cards(engine):
    observe(engine, [observation()])
    cid = identity(engine.card_registry)
    assert engine.apply_correction(cid, 1, "not_a_card").ok
    engine.capture.monitor.timestamp = 20.
    with engine._lock:
        DetectionEngine._observe_registry(engine, [observation() | {"class": "2"}])
        engine.publish_snapshot()
    snap = engine.get_snapshot()
    assert not snap["reading"]["present"] and not snap["reading"]["count_pending"]
    assert snap["card_groups"] == [] and snap["dealer_cards"] == []
    assert engine.counter.cards_seen == 0
    assert build_table_presentation(snap, selected_mode="individual", running=True)["status"] == "waiting"
