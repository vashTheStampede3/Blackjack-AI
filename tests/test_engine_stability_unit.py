"""Fast regressions for mode validation, stale legacy edits, and side-bet gates."""
from types import SimpleNamespace
import threading
import time

import pytest

from lib.logic import engine as engine_module
from lib.logic.engine import DetectionEngine


def _monitor_engine(monkeypatch, mode, layout=None, custom=None):
    e = object.__new__(DetectionEngine)
    e.capture = SimpleNamespace(resolution=(1280, 720), set_monitor=lambda m: None)
    e.card_layout = None
    e.counter = SimpleNamespace(cards_seen=0, shoe_id="shoe-1")
    e._lock = threading.RLock()
    e._unread_positions = {}
    e._round_observation = SimpleNamespace(count_uncertain=False)
    e._card_evidence = object()
    e._pending_extra, e._pending_positions = {}, {}
    e._prev_seat_thumbs = {}
    e._prev_thumb = e._prev_dealer_thumb = None
    e.round_number = 1
    e._anchor_state, e._anchor_set = {}, {}
    e.phase_detector = SimpleNamespace(configure=lambda r: None)
    e.log = lambda *a, **k: None
    monkeypatch.setattr(engine_module.region_profiles, "active_name", lambda: "demo")
    monkeypatch.setattr(engine_module.region_profiles, "layout_mode", lambda *a, **k: mode)
    monkeypatch.setattr(engine_module.region_profiles, "get_card_layout", lambda *a, **k: layout)
    monkeypatch.setattr(engine_module, "load_custom_regions", lambda *a, **k: custom)
    monkeypatch.setattr(engine_module.ocr, "load_regions", lambda r: None)
    return e


def test_selected_table_mode_does_not_require_seven_seat_regions(monkeypatch):
    from lib.logic.app_modes import MULTIPLAYER
    from lib.logic.card_layout import CardLayout
    layout = CardLayout.from_payload({
        "mode": MULTIPLAYER, "resolution": [1280, 720],
        "table": [[0, 0], [500, 0], [500, 500], [0, 500]],
        "mine": [[10, 100], [100, 100], [100, 200], [10, 200]],
        "dealer": [[600, 100], [700, 100], [700, 200], [600, 200]],
    }, (1280, 720))
    monkeypatch.setattr(engine_module.region_profiles, "layout_mode", lambda *a, **k: MULTIPLAYER)
    monkeypatch.setattr(engine_module.region_profiles, "get_card_layout", lambda *a, **k: layout)
    monkeypatch.setattr(engine_module.region_profiles, "active_name", lambda: "demo")
    monkeypatch.setattr(engine_module.region_profiles, "get_source_context", lambda *a, **k: None)
    from lib.logic.engine import DetectionEngine
    class Provider:
        backend_name = "test"
        def players_model(self): return object()
    e = DetectionEngine(provider=Provider(), persist=False, log=lambda *a, **k: None)
    e.set_monitor(SimpleNamespace(width=1280, height=720), profile="demo")
    assert e.card_layout.mode == layout.mode
    assert e.card_layout.to_payload() == layout.to_payload()
    assert e._active_mode == MULTIPLAYER
    for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
        getattr(e, name).shutdown(wait=True, cancel_futures=True)


def test_seven_seat_profile_without_regions_still_rejects(monkeypatch):
    from lib.logic.app_modes import INDIVIDUAL
    e = _monitor_engine(monkeypatch, INDIVIDUAL, custom=None)
    with pytest.raises(ValueError, match="calibraci.n Individual"):
        e.set_monitor(SimpleNamespace(width=1280, height=720), profile="demo")
    assert not hasattr(e, "_active_profile")


def test_legacy_correction_rejects_stale_round_shoe_and_content_without_mutation():
    e = object.__new__(DetectionEngine)
    e.round_number = 4
    e.counter = SimpleNamespace(shoe_id="shoe-a")
    e.card_registry = None
    e._lock = threading.RLock()
    e.seats = [SimpleNamespace(cards=[{"name": "8", "counted": True}])]
    e.replace_card = lambda *a, **k: pytest.fail("stale correction mutated ledger")
    ref = {"seat_index": 0, "card_index": 0}
    assert not e.correct_legacy_card(ref, expected_round=3, expected_shoe="shoe-a",
                                     expected_name="8", action="set_rank", new_rank="9")
    assert not e.correct_legacy_card(ref, expected_round=4, expected_shoe="shoe-b",
                                     expected_name="8", action="set_rank", new_rank="9")
    assert not e.correct_legacy_card(ref, expected_round=4, expected_shoe="shoe-a",
                                     expected_name="7", action="set_rank", new_rank="9")
    assert not e.correct_legacy_card(ref, expected_round=4, expected_shoe="shoe-a",
                                     expected_name="8", action="set_rank", new_rank="9",
                                     new_suit="H")
    assert e.seats[0].cards[0]["name"] == "8"


def test_disabled_side_bets_skip_composition_and_submit(monkeypatch):
    e = object.__new__(DetectionEngine)
    e._advice_lock = threading.Lock()
    e._sidebet_result = ([], None)
    e._sidebet_pending = False
    e._advice_pool = SimpleNamespace(submit=lambda *a, **k: pytest.fail("side-bet job submitted"))
    monkeypatch.setattr(engine_module.constants, "SIDE_BETS",
                        {"one": {"enabled": False}, "two": {"enabled": False}})
    assert e._side_bet_evs({}) == []


def test_headless_constructor_publishes_snapshot_without_monitor(monkeypatch):
    class Provider:
        backend_name = "unit"
    monkeypatch.setitem(engine_module.constants.BETTING, "use_exact_edge", False)
    for cfg in engine_module.constants.SIDE_BETS.values():
        monkeypatch.setitem(cfg, "enabled", False)
    e = DetectionEngine(provider=Provider(), persist=False)
    try:
        snapshot = e.get_snapshot()
        assert snapshot is not None
        assert snapshot["source_resolution"] is None
        assert snapshot["source_profile"] is None
    finally:
        e._pool.shutdown(wait=False, cancel_futures=True)
        e._advice_pool.shutdown(wait=False, cancel_futures=True)
        e._io_pool.shutdown(wait=False, cancel_futures=True)
        e._predeal_pool.shutdown(wait=False, cancel_futures=True)


def test_preview_cache_requires_fresh_matching_source_without_inference():
    e = object.__new__(DetectionEngine)
    layout = object()
    e._preview_cache_lock = threading.Lock()
    e.capture = SimpleNamespace(monitor=object(), resolution=(1280, 720))
    e._active_profile, e._active_mode, e.card_layout = "profile", "table", layout
    frame = SimpleNamespace(copy=lambda: "frame-copy")
    e._preview_detection_cache = {
        "timestamp": time.monotonic(), "frame": frame,
        "resolution": [1280, 720], "profile": "profile", "mode": "table",
        "layout": layout, "bounds": [0, 0, 1280, 720], "input_size": [1280, 720],
        "predictions": [{"name": "8"}],
    }
    result = e.get_region_preview_cache()
    assert result["frame"] == "frame-copy"
    assert result["predictions"] == [{"name": "8"}]
    e._active_profile = "changed"
    assert e.get_region_preview_cache() is None


def test_capture_resolution_mismatch_skips_inference_and_preserves_shoe_round():
    import numpy as np
    e = object.__new__(DetectionEngine)
    e.capture = SimpleNamespace(grab_bgr=lambda: np.zeros((6, 8, 3), dtype=np.uint8),
                                resolution=(4, 6))
    e.card_layout = None
    e._source_mismatch = None
    e._round_observation = SimpleNamespace(captured_at=0., inferred_at=42.,
                                           dealer_ok=True, seats={0: True})
    e.counter = SimpleNamespace(cards_seen=12)
    e.round_number = 9
    e._lock = threading.RLock()
    e._metrics = {"cycle_ms": 0., "skipped": False}
    e._last_activity = "waiting"
    e.last_error = None
    e.log = lambda *a, **k: None
    e.provider = SimpleNamespace(players_model=lambda: pytest.fail("inference attempted"))
    e._activity = lambda: "waiting"
    e._has_pending_detection = lambda: False
    e._dealer_drawing = lambda: False
    e.publish_snapshot = lambda: None
    e.run_cycle()
    assert "resolution changed" in e.last_error.lower()
    assert e._source_mismatch
    assert e.counter.cards_seen == 12 and e.round_number == 9
    assert not e._round_observation.dealer_ok
    assert e._round_observation.inferred_at == 0.


def test_loaded_model_metadata_is_cached_once_per_loaded_object():
    e = object.__new__(DetectionEngine)
    model = object()
    calls = []
    e.provider = SimpleNamespace(loaded_model_metadata=lambda kind: calls.append(kind) or {
        "artifact": "cards.onnx", "version": "v1", "quantization": "int8",
        "input_size": 640, "path_secret": "must not pass through"})
    e._metadata_model_refs = {}
    e._model_metadata_cache = {"players": None, "dealer": None}
    e._model_metadata_lock = threading.Lock()
    e._cache_loaded_model_metadata("players", model)
    e._cache_loaded_model_metadata("players", model)
    assert calls == ["players"]
    assert e._model_metadata_cache["players"] == {
        "artifact": "cards.onnx", "version": "v1", "quantization": "int8",
        "input_size": 640, "input_size_known": None}
