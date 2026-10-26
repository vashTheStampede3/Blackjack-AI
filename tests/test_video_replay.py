"""Replay plumbing checks with generated frames; not recognition measurements."""
import socket
from types import SimpleNamespace

import numpy as np
import pytest

from lib.common import constants
from lib.logic import region_profiles
from tools.replay_video import ReplaySource, ReplayEngine, TraceCounter, no_network, summarize
from tests.test_video_source import video
from lib.logic.video_source import VideoFile
from lib.logic.card_layout import CardLayout
from lib.logic.app_modes import INDIVIDUAL
from lib.logic.table_inference import prepare_layout_rois


def test_replay_position_follows_elapsed_time_not_number_of_cycles(video):
    clock = [10.]
    with VideoFile(video) as reader:
        source = ReplaySource(reader, .1, .6, clock=lambda: clock[0])
        source.begin()
        assert source.grab_bgr().shape == (180, 320, 3)
        assert source.frame_index == 1
        clock[0] += .3
        source.grab_bgr()
        assert source.frame_index == 4
        clock[0] += .3
        with pytest.raises(EOFError):
            source.grab_bgr()


def test_network_is_blocked_and_restored():
    original = socket.socket.connect
    with no_network(), socket.socket() as connection:
        with pytest.raises(RuntimeError, match="forbidden"):
            connection.connect(("127.0.0.1", 1))
    assert socket.socket.connect is original


def test_only_last_undecodable_frame_is_treated_as_end():
    def fail(seconds):
        raise ValueError("decode failed")
    reader = SimpleNamespace(resolution=(320, 180), last_frame_seconds=.9, fps=10, frame_at=fail)
    clock = [0.]
    source = ReplaySource(reader, 0., 1., clock=lambda: clock[0])
    source.begin()
    clock[0] = .4
    with pytest.raises(ValueError, match="decode failed"):
        source.grab_bgr()
    assert not source.ended
    clock[0] = .89
    with pytest.raises(EOFError):
        source.grab_bgr()
    assert source.ended


def test_explicit_profile_and_injected_model_use_production_counting_without_db(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    for cfg in (constants.OCR, constants.PHASE, constants.ANCHORS, constants.TRAINING):
        monkeypatch.setitem(cfg, "enabled", 0)
    monkeypatch.setattr("lib.logic.engine.SessionStore", lambda: pytest.fail("User DB used"))
    monkeypatch.setattr("lib.logic.engine.ModelProvider.get", lambda: pytest.fail("Automatic backend used"))
    def save_layout(resolution, profile):
        w, h = resolution
        layout = CardLayout.from_payload({
            "mode": INDIVIDUAL, "resolution": [w, h],
            "mine": [[5, 80], [70, 80], [70, 170], [5, 170]],
            "dealer": [[100, 5], [210, 5], [210, 65], [100, 65]],
        }, resolution)
        region_profiles.set_card_layout(layout, profile=profile, overwrite=True)
    save_layout((320, 180), "Measured")
    save_layout((640, 360), "Other active")
    predictions = [{"class": "5", "cx": 20., "cy": 120., "width": 15., "height": 25., "confidence": .95},
                   {"class": "7", "cx": 150., "cy": 30., "width": 15., "height": 25., "confidence": .95}]
    measured = region_profiles.get_card_layout((320, 180), INDIVIDUAL, profile="Measured")
    rois = prepare_layout_rois(np.zeros((180, 320, 3), np.uint8), measured)
    def infer(image, *args):
        l, t, r, b = next(bounds for _, bounds, crop in rois if crop.shape == image.shape)
        return [dict(p, cx=p["cx"]-l, cy=p["cy"]-t) for p in predictions
                if l <= p["cx"] < r and t <= p["cy"] < b]
    provider = SimpleNamespace(backend_name="Synthetic plumbing test", players_model=lambda: SimpleNamespace(
        hosted=False, predict=infer),
        dealer_model=lambda: SimpleNamespace(predict=lambda *a: []))
    source = SimpleNamespace(width=320, height=180, timestamp=.1,
                             grab_bgr=lambda: np.full((180, 320, 3), 40, np.uint8))
    engine = ReplayEngine(provider=provider, persist=False, log=lambda *a, **k: None)
    try:
        with pytest.raises(ValueError, match="calibraci.n Individual"):
            engine.set_monitor(source, profile="Missing")
        assert engine.capture.monitor is None
        engine.set_monitor(source, profile="Measured")
        engine.counter = TraceCounter(engine, source)
        engine.card_registry.counter = engine.counter
        monkeypatch.setattr(constants, "EXTRA_CARD_CONFIRM_CYCLES", 1)
        engine.run_cycle()
        engine.run_cycle()
        engine.run_cycle()
        assert engine.counter.cards_seen == 2
        assert engine.counter.running_count == 1
        assert {e["owner"] for e in engine.counter.events} == {"mine", "dealer"}
        assert engine.get_snapshot()["count"]["cards_seen"] == 2
        assert region_profiles.active_name() == "Other active"
        assert not list(tmp_path.glob("*.db"))
    finally:
        for pool in (engine._pool, engine._io_pool, engine._advice_pool, engine._predeal_pool):
            pool.shutdown(wait=True, cancel_futures=True)


def test_scoring_boundaries_only_summarize_existing_events():
    labels = {"rounds": [{"round": 1, "user_deal_interval_seconds": [10, 12], "expected_cards": 1,
                          "expected_hilo_delta": 1, "expected_cumulative_running_count": 1},
                         {"round": 2, "user_start_seconds": 20, "expected_cards": 1,
                          "expected_hilo_delta": -1, "expected_cumulative_running_count": 0}]}
    events = [{"seconds": 11, "kind": "count", "running": 1},
              {"seconds": 21, "kind": "count", "running": 0}]
    assert [row["delta"] for row in summarize(events, labels)] == [1, -1]
