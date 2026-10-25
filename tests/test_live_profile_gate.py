"""Live detection must not silently replace a selected calibration."""
from types import SimpleNamespace

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


def test_selected_new_layout_is_not_silently_replaced_by_legacy_defaults(engine):
    value, _ = engine
    layout = CardLayout.from_payload({
        "mode": "table", "resolution": [640, 480],
        "table": [[10, 200], [620, 200], [620, 470], [10, 470]],
        "mine": [[200, 300], [300, 300], [300, 450], [200, 450]],
        "dealer": [[450, 20], [600, 20], [600, 100], [450, 100]],
    }, (640, 480))
    region_profiles.set_card_layout(layout, profile="Only new mode")
    with pytest.raises(ValueError, match="seven-seat"):
        value.set_monitor(source())


def test_video_calibration_is_not_applied_to_live_monitor(engine):
    value, _ = engine
    players = [[[10, 200], [50, 200], [50, 250], [10, 250]]] * 7
    region_profiles.set_regions((640, 480), {
        "players": players, "dealer": [400, 10, 600, 90],
        "reference_source": {"kind": "video_frame", "path": "video.mp4"},
    }, profile="Video only")
    with pytest.raises(ValueError, match="video"):
        value.set_monitor(source())


def test_default_without_calibration_reports_scaled_regions(engine):
    value, entries = engine
    value.set_monitor(source())
    assert value.regions and value._dealer_rect
    assert any("default" in message.lower() for message, _ in entries)


def test_main_window_keeps_calibration_available_when_profile_is_missing():
    controls, notices = {}, []

    def widget(name):
        return SimpleNamespace(config=lambda **values: controls.setdefault(name, {}).update(values))

    def reject(_source):
        raise ValueError("Missing exact calibration")

    window = SimpleNamespace(
        monitor_combo=SimpleNamespace(current=lambda: 0), _monitors=[source()],
        source_kind=SimpleNamespace(get=lambda: "Monitor"),
        controller=SimpleNamespace(set_monitor=reject),
        start_btn=widget("start"), regions_btn=widget("preview"),
        calibrate_btn=widget("calibrate"), monitor_status=widget("status"),
        set_status=lambda message, error=False: notices.append((message, error)),
    )
    ModernBlackjackGUI._confirm_monitor(window)
    assert controls["start"]["state"] == "disabled"
    assert controls["calibrate"]["state"] == "normal"
    assert notices == [("Missing exact calibration", True)]
