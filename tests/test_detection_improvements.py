"""Local, deterministic detection regression tests. No casino or result files."""
from types import SimpleNamespace
import numpy as np
import pytest

from lib.common import constants
from lib.logic.monitor_utils import Polygon
from lib.logic.table_inference import predict_table, table_bounds


def test_single_crop_restores_original_coordinates_and_preserves_input(monkeypatch):
    monkeypatch.setattr(constants, "TABLE_CROP_ENABLED", 1)
    frame = np.full((1200, 1920, 3), 40, np.uint8)
    regions = [Polygon([(900, 800), (1100, 800), (1100, 1000)])]
    dealer = (900, 650, 1000, 720)
    calls = []
    def predict(image, *args):
        calls.append(image.shape)
        return [{"cx": 40., "cy": 60., "width": 20., "height": 30.,
                 "class": "A", "confidence": .95}, {"cx": -5, "cy": 0}]
    preds, bounds = predict_table(SimpleNamespace(hosted=False, predict=predict),
                                  frame, regions, dealer, 55, 45)
    l, t, r, b = bounds
    assert len(calls) == 1 and calls[0] == (b-t, r-l, 3)
    assert r-l < 1920 and b-t < 1200
    assert preds[0]["cx"] == l+40 and preds[0]["cy"] == t+60
    assert preds[0]["width"] == 20 and len(preds) == 1
    assert np.all(frame == 40)


def test_crop_includes_every_seat_and_expanded_dealer_band():
    regions = [Polygon([(100, 800), (200, 800), (200, 1000)]),
               Polygon([(1700, 800), (1800, 800), (1800, 1000)])]
    l, t, r, b = table_bounds((1200, 1920, 3), regions, (900, 650, 1000, 720))
    assert l <= 100 and r >= 1800 and t <= 650 and b >= 1000
    assert l <= 750 and r >= 1150


def test_rollback_and_hosted_keep_full_frame(monkeypatch):
    frame = np.zeros((480, 640, 3), np.uint8)
    regions = [Polygon([(100, 200), (200, 200), (200, 300)])]
    for enabled, hosted in [(0, False), (1, True)]:
        monkeypatch.setattr(constants, "TABLE_CROP_ENABLED", enabled)
        calls = []
        model = SimpleNamespace(hosted=hosted, predict=lambda image, *a: calls.append(image.shape) or [])
        _, bounds = predict_table(model, frame, regions, (200, 100, 250, 150), 55, 45)
        assert calls == [frame.shape] and bounds == (0, 0, 640, 480)


@pytest.fixture
def engine(monkeypatch):
    from lib.logic.engine import DetectionEngine
    from lib.logic.models import ModelProvider
    monkeypatch.setitem(constants.BETTING, "use_exact_edge", 0)
    monkeypatch.setitem(constants.TRAINING, "enabled", 0)
    monkeypatch.setitem(constants.OCR, "enabled", 0)
    monkeypatch.setitem(constants.PHASE, "enabled", 0)
    monkeypatch.setattr("lib.logic.engine.SessionStore", lambda: None)
    monkeypatch.setattr(DetectionEngine, "_warm_advice_job", lambda *a: None)
    monkeypatch.setattr(DetectionEngine, "_optimal_advice", lambda *a, **k: ("", "white", None))
    monkeypatch.setattr(ModelProvider, "get", lambda: SimpleNamespace(backend_name="Local test"))
    value = DetectionEngine(log=lambda *a, **k: None)
    value.capture.set_monitor(SimpleNamespace(width=640, height=480))
    value.regions = [Polygon([(0, 150), (300, 150), (300, 400), (0, 400)])]
    value._dealer_rect = (0, 0, 100, 100)
    yield value
    for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
        getattr(value, name).shutdown(wait=True, cancel_futures=True)


def pred(rank, x=80, y=200):
    return {"class": rank, "cx": x, "cy": y, "width": 30, "height": 40, "confidence": .95}


def test_first_pair_confirms_together_and_repeated_frames_never_recount(engine):
    pair = [pred("10", 80), pred("10", 102)]
    engine._process_players(pair)
    assert engine.counter.cards_seen == 0 and engine._has_pending_detection()
    engine._process_players(pair)
    assert engine.counter.cards_seen == 2 and len(engine.seats[0].cards) == 2
    for _ in range(5):
        engine._process_players(pair)
    assert engine.counter.cards_seen == 2 and not engine._pending_extra


def test_jitter_does_not_reset_votes_but_rank_flicker_does(engine):
    engine._process_players([pred("6", 99.8)])
    engine._process_players([pred("9", 100.2)])
    engine._process_players([pred("6", 99.9)])
    assert engine.counter.cards_seen == 0
    engine._process_players([pred("6", 100.1)])
    assert engine.counter.cards_seen == 1 and engine.seats[0].cards[0]["name"] == "6"


def test_single_frame_duplicates_are_not_votes(engine):
    box = pred("5")
    engine._process_players([box, box.copy()])
    assert engine.counter.cards_seen == 0
    engine._process_players([box, box.copy()])
    assert engine.counter.cards_seen == 1


def test_initial_confirmation_rollback(engine, monkeypatch):
    monkeypatch.setattr(constants, "CONFIRM_INITIAL_CARDS_ENABLED", 0)
    engine._process_players([pred("5")])
    assert engine.counter.cards_seen == 1


def test_small_seat_change_is_not_hidden_by_global_frame_diff(engine):
    engine.regions = [Polygon([(80, 200), (160, 200), (160, 300), (80, 300)])]
    frame = np.zeros((480, 640, 3), np.uint8)
    assert not engine._frame_unchanged(frame)
    assert engine._frame_unchanged(frame)
    frame[210:240, 90:120] = 255
    assert not engine._frame_unchanged(frame)


def test_static_frame_pending_cards_still_get_a_second_real_inference(engine, monkeypatch):
    calls = []
    predictions = [pred("A", 50, 50), pred("10", 80), pred("Q", 180)]
    def predict(*args):
        calls.append(1)
        return [p.copy() for p in predictions]
    engine.provider = SimpleNamespace(backend_name="Local test",
        players_model=lambda: SimpleNamespace(hosted=False, predict=predict),
        dealer_model=lambda: SimpleNamespace(hosted=False, predict=lambda *a: []))
    monkeypatch.setattr(engine.capture, "grab_bgr", lambda: np.full((480, 640, 3), 50, np.uint8))
    assert engine.run_cycle() == "dealing"
    assert engine.counter.cards_seen == 0
    engine.run_cycle()
    assert len(calls) == 2 and engine.counter.cards_seen == 3


def test_ambiguous_rank_stays_unread_until_two_clear_observations(engine):
    uncertain = pred("K") | {"ambiguous": True}
    for _ in range(3):
        engine._process_players([uncertain])
    assert engine.counter.cards_seen == 0 and engine._unread_positions
    # K/Q share Hi-Lo, but we must not fabricate a rank or pair.
    engine._process_players([pred("Q")])
    assert engine.counter.cards_seen == 0
    engine._process_players([pred("Q")])
    assert engine.counter.cards_seen == 1 and not engine._unread_positions
    assert engine.seats[0].cards[0]["name"] == "Queen"


def test_unread_card_disappearing_does_not_silently_certify_shoe(engine):
    engine._process_players([pred("6") | {"ambiguous": True}])
    engine._process_players([])
    assert not engine._pending_extra and engine._unread_positions
    engine.new_round()
    assert engine._round_observation.count_uncertain
    assert engine.counter.cards_seen == 0


def test_dealer_motion_breaks_confirmation_instead_of_becoming_a_card(engine):
    card = pred("A", 50, 50)
    engine._process_dealer([card])
    engine._process_dealer([card | {"unstable": True}])
    engine._process_dealer([card])
    assert not engine.dealer_locked
    engine._process_dealer([card])
    assert engine.dealer_locked and engine.counter.cards_seen == 1
    assert not engine._unread_positions


def test_local_motion_gate_ignores_moving_background_but_holds_changed_card():
    from lib.logic.card_evidence import CardEvidence
    evidence = CardEvidence()
    frame = np.full((480, 640, 3), 40, np.uint8)
    box = pred("8")
    assert not evidence.observe(frame, [box])[0]["unstable"]
    frame[0:100] = 200  # animated dealer/video far outside the card
    assert not evidence.observe(frame, [box])[0]["unstable"]
    frame[180:220, 65:95] = 220
    assert evidence.observe(frame, [box])[0]["unstable"]
    assert not evidence.observe(frame, [box])[0]["unstable"]
    assert evidence.observe(frame, [box | {"cx": 88}])[0]["unstable"]


def test_local_motion_gate_does_not_treat_box_jitter_as_card_motion():
    from lib.logic.card_evidence import CardEvidence
    frame = np.random.default_rng(7).integers(0, 256, (180, 240, 3), dtype=np.uint8)
    box = pred("9", 100, 100) | {"width": 64., "height": 35.}
    jittered = box | {"cx": 101., "width": 65., "height": 34.}
    evidence = CardEvidence()
    evidence.observe(frame, [box])
    assert not evidence.observe(frame, [jittered])[0]["unstable"]


def test_decode_preserves_runner_up_score_and_can_disable_abstention(monkeypatch):
    from lib.logic.models import OnnxModel
    model = object.__new__(OnnxModel)
    model._names = {0: "6", 1: "9"}
    raw = np.zeros((6, 12), np.float32)
    raw[:4, 0] = [40, 50, 20, 30]
    raw[4:, 0] = [.8, .76]
    result = model._decode(raw, .55, .45, 1, 0, 0)
    assert result[0]["class"] == "6" and result[0]["runner_up_class"] == "9"
    assert result[0]["ambiguous"] and result[0]["class_margin"] == pytest.approx(.04)
    monkeypatch.setattr(constants, "CARD_EVIDENCE_ENABLED", 0)
    assert not model._decode(raw, .55, .45, 1, 0, 0)[0]["ambiguous"]


def test_manual_add_resolves_one_unread_card_without_later_double_count(engine):
    engine._process_players([pred("6") | {"ambiguous": True}])
    engine.replace_card(0, 0, "6")
    assert not engine._unread_positions and not engine._pending_extra
    assert engine.counter.cards_seen == 1
    for _ in range(3):
        engine._process_players([pred("6")])
    assert engine.counter.cards_seen == 1
