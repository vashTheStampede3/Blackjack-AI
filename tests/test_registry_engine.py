"""Engine integration with invented observations: logic, not real-card reading."""
from types import SimpleNamespace

import pytest

from lib.common import constants
from lib.logic.card_layout import CardLayout
from lib.logic import region_profiles
from lib.logic.monitor_utils import Polygon
from tools.replay_video import ReplayEngine


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(constants.TRAINING, "enabled", 0)
    value = ReplayEngine(provider=SimpleNamespace(backend_name="Synthetic integration"), persist=False,
                         log=lambda *a, **k: None)
    value.capture.set_monitor(SimpleNamespace(width=640, height=480, timestamp=0.))
    value.regions = [Polygon([[0, 180], [80, 180], [80, 450], [0, 450]])]
    zones = CardLayout.from_payload({"mode": "table", "resolution": [640, 480],
        "table": [[0, 180], [640, 180], [640, 470], [0, 470]],
        "mine": [[5, 200], [70, 200], [70, 440], [5, 440]],
        "dealer": [[400, 20], [600, 20], [600, 100], [400, 100]]}, (640, 480))
    value.use_card_layout(zones, memory="registry")
    yield value
    for pool in (value._pool, value._io_pool, value._advice_pool, value._predeal_pool):
        pool.shutdown(wait=True, cancel_futures=True)


def observation(rank, x, role="other"):
    return {"class": rank, "cx": x, "cy": 300. if role != "dealer" else 50.,
            "width": 20., "height": 30., "owner": role, "confidence": .95}


def observe(engine, boxes, now):
    engine.capture.monitor.timestamp = now
    engine._round_observation.captured_at = 100.+now
    engine._observe_registry(boxes)
    engine.publish_snapshot()


def test_common_count_is_not_limited_by_old_seat_boxes_or_six_card_limit(engine):
    boxes = [observation("2", 100+i*45) for i in range(9)]
    observe(engine, boxes, 1.)
    observe(engine, boxes, 2.)
    assert engine.counter.cards_seen == 9 and engine.counter.running_count == 9
    assert len(engine.get_snapshot()["card_registry"]) == 9
    assert all(not s.cards for s in engine.seats)  # outside old seat polygon, still counted
    assert engine.card_registry.counter is engine.counter


def test_doubt_withholds_own_advice_and_survives_clear_save_restore(engine):
    boxes = [observation("5", 20, "mine"), observation("6", 50, "mine"), observation("7", 450, "dealer")]
    observe(engine, boxes, 1.)
    observe(engine, boxes, 2.)
    assert engine.counter.cards_seen == 3
    card = next(c for c in engine.card_registry.cards.values() if c.owner == "mine")
    engine.card_registry._issue(card, "Rango cambiado; revisar carta")
    engine.publish_snapshot()
    snap = engine.get_snapshot()
    assert snap["count"]["count_uncertain"] and snap["reading"]["count_uncertain"]
    assert snap["seats"][0]["optimal_action"] is None
    engine.new_round()
    state = engine._shoe_state()
    assert state["count_uncertain"] and state["uncertainties"]
    engine.counter.reset_shoe()
    engine.apply_shoe_state({"state": state, "round_number": 2})
    assert engine.counter.count_uncertain and engine._round_observation.count_uncertain
    assert not engine.card_registry.cards


def test_correction_of_other_players_uses_id_and_refreshes_real_count(engine):
    boxes = [observation("2", 300)]
    observe(engine, boxes, 1.)
    observe(engine, boxes, 2.)
    identity = next(iter(engine.card_registry.cards))
    engine.correct_tracked_card(identity, "King", expected_round=1)
    assert engine.counter.cards_seen == 1 and engine.counter.running_count == -1
    assert engine.get_snapshot()["card_registry"][0]["rank"] == "King"


def test_restored_current_round_ledger_does_not_recount_seen_cards(engine):
    boxes = [observation("2", 300)]
    observe(engine, boxes, 1.)
    observe(engine, boxes, 2.)
    state = engine._shoe_state()
    engine.card_registry.clear_round()
    engine.apply_shoe_state({"state": state, "round_number": 1})
    observe(engine, boxes, 3.)
    assert engine.counter.cards_seen == 1 and len(engine.card_registry.cards) == 1


def test_new_shoe_detaches_old_registry_cards_until_clear(engine):
    boxes = [observation("2", 300)]
    observe(engine, boxes, 1.)
    observe(engine, boxes, 2.)
    identity = next(iter(engine.card_registry.cards))
    engine.reset_shoe()
    observe(engine, boxes, 3.)
    observe(engine, boxes, 4.)
    assert engine.counter.cards_seen == 0 and engine.counter.count_uncertain
    assert engine.counter.uncertainties["shoe_reset_with_cards"]["counted"] is False
    with pytest.raises(ValueError, match="shoe"):
        engine.correct_tracked_card(identity, "King")


def test_zero_count_uncertainty_is_actually_saved_on_round_clear(engine, tmp_path):
    from lib.logic.session_store import SessionStore
    engine.store = SessionStore(tmp_path / "uncertain.db")
    observe(engine, [observation("unread-card", 300)], 1.)
    assert engine.counter.cards_seen == 0 and engine.counter.count_uncertain
    engine.new_round()
    engine._io_pool.submit(lambda: None).result(timeout=5)
    info = engine.store.load_recent_shoe_state()
    assert info is not None, "A round with only an unread card must survive restart"
    assert info["state"]["cards_seen"] == 0 and info["state"]["count_uncertain"]
    engine.counter.reset_shoe()
    engine.apply_shoe_state(info)
    assert engine.counter.count_uncertain and engine._round_observation.count_uncertain


def test_card_id_corrections_change_the_shoe_once_and_reject_old_round(engine):
    observe(engine, [observation("unread-card", 300)], 1.)
    identity = next(iter(engine.card_registry.cards))
    assert engine.counter.cards_seen == 0 and engine.counter.count_uncertain
    first = engine.apply_correction(identity, 1, "set_rank", new_rank="10")
    assert first.ok and first.rc_delta == -1
    assert engine.counter.cards_seen == 1 and engine.counter.running_count == -1
    assert engine.counter.seen_counts_by_rank()["10"] == 1
    second = engine.apply_correction(identity, 1, "set_rank", new_rank="5")
    assert second.ok and second.rc_delta == 2
    assert engine.counter.cards_seen == 1 and engine.counter.running_count == 1
    counts = engine.counter.seen_counts_by_rank()
    assert counts["10"] == 0 and counts["5"] == 1
    engine.correct_tracked_card(identity, action="not_a_card", expected_round=1)
    assert engine.counter.cards_seen == 0 and engine.counter.running_count == 0
    assert engine.card_registry.cards[identity].status == "not_a_card"
    state = engine.counter.get_state()
    engine.new_round()
    stale = engine.apply_correction(identity, 1, "set_rank", new_rank="6")
    assert not stale.ok and stale.reason == "round_changed"
    assert engine.counter.get_state() == state


def test_live_monitor_activates_saved_table_layout_and_registry(engine):
    zones = engine.card_layout
    region_profiles.set_card_layout(zones, profile=region_profiles.DEFAULT_NAME)
    engine.set_monitor(SimpleNamespace(width=640, height=480, timestamp=0.))
    assert engine.card_layout == zones
    assert engine._memory_mode == "registry"
    assert engine.card_registry is not None


def test_store_keeps_uncertainty_even_when_no_card_was_counted(engine, tmp_path):
    from lib.logic.session_store import SessionStore
    store = SessionStore(tmp_path / "zero.db")
    engine.counter.mark_uncertainty("unread", {"reason": "Carta sin leer"})
    store.save_shoe_state(engine.counter.get_state(), 1, False)
    info = store.load_recent_shoe_state()
    assert info is not None and info["state"]["count_uncertain"]
