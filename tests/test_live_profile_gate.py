"""Live detection must not silently replace a selected calibration."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.common import constants
from lib.logic import region_profiles
from lib.logic.card_layout import CardLayout
from tools.replay_video import ReplayEngine
from lib.interfaces.modern_gui import ModernBlackjackGUI


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    entries = []
    value = ReplayEngine(provider=SimpleNamespace(backend_name="fake"), persist=False,
                         log=lambda message, level=None: entries.append((message, level)))
    yield value, entries
    for pool in (value._pool, value._io_pool, value._advice_pool, value._predeal_pool):
        pool.shutdown(wait=True, cancel_futures=True)


def source():
    return SimpleNamespace(width=640, height=480)


def test_selected_new_layout_is_used_without_historical_seat_fallback(engine):
    value, _ = engine
    layout = CardLayout.from_payload({
        "mode": "table", "resolution": [640, 480],
        "table": [[10, 200], [620, 200], [620, 470], [10, 470]],
        "mine": [[200, 300], [300, 300], [300, 450], [200, 450]],
        "dealer": [[450, 20], [600, 20], [600, 100], [450, 100]],
    }, (640, 480))
    region_profiles.set_card_layout(layout, profile="Only new mode")
    value.set_monitor(source())
    assert value.card_layout.mode == "multiplayer"


def test_video_calibration_is_not_applied_to_live_monitor(engine):
    value, _ = engine
    layout = CardLayout.from_payload({
        "mode": "individual", "resolution": [640, 480],
        "mine": [[10, 200], [200, 200], [200, 450], [10, 450]],
        "dealer": [[400, 10], [600, 10], [600, 90], [400, 90]],
        "reference_source": {"kind": "video_frame", "path": "video.mp4"},
    }, (640, 480))
    region_profiles.set_card_layout(layout, profile="Video only")
    with pytest.raises(ValueError, match="video"):
        value.set_monitor(source())


def test_default_without_calibration_reports_scaled_regions(engine):
    value, entries = engine
    with pytest.raises(ValueError, match="calibraci"):
        value.set_monitor(source())
    assert value.card_layout is None
    assert not any("scaled region" in message.lower() for message, _ in entries)


def test_main_window_reports_exact_missing_regions_and_keeps_calibration_available(monkeypatch):
    controls, notices = {}, []

    def widget(name):
        return SimpleNamespace(config=lambda **values: controls.setdefault(name, {}).update(values))

    monkeypatch.setattr(region_profiles, "active_name", lambda: "Missing")
    monkeypatch.setattr(region_profiles, "has_card_layout", lambda *a, **k: False)
    monkeypatch.setattr(region_profiles, "get_card_layout", lambda *a, **k: None)
    monkeypatch.setattr(region_profiles, "missing_card_regions",
                        lambda *a, **k: ("mine", "dealer"))
    window = SimpleNamespace(
        monitor_combo=SimpleNamespace(current=lambda: 0), _monitors=[source()],
        source_kind=SimpleNamespace(get=lambda: "Monitor"),
        _sync_mode_context=lambda *_: None,
        _selected_app_mode=lambda: "individual",
        controller=SimpleNamespace(set_monitor=Mock()),
        start_btn=widget("start"), regions_btn=widget("preview"),
        calibrate_btn=widget("calibrate"), ocr_btn=widget("ocr"),
        controls_btn=widget("controls"), anchors_btn=widget("anchors"),
        monitor_status=widget("status"), ui_actions=SimpleNamespace(current=None),
        set_status=lambda message, error=False: notices.append((message, error)),
    )
    ModernBlackjackGUI._confirm_monitor(window)
    assert controls["start"]["state"] == "disabled"
    assert controls["calibrate"]["state"] == "normal"
    assert window.controller.set_monitor.call_count == 0
    assert len(notices) == 1 and notices[0][1]
    assert "Mi asiento" in notices[0][0] and "Crupier" in notices[0][0]
