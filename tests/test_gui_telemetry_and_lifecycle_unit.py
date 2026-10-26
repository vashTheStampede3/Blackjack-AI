"""HUD and lifecycle contracts exercised with fake windows and workers."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.interfaces.modern_gui import ModernBlackjackGUI as GUI
from lib.interfaces.round_overlay import MiniTableOverlay
from lib.logic.app_modes import APP_MODE_LABELS, INDIVIDUAL
from lib.logic.background import DetectionController


def _snapshot():
    return {
        "seq": 11,
        "round": 6,
        "shoe_id": "shoe-1",
        "source_mode": INDIVIDUAL,
        "source_profile": "Live",
        "source_resolution": [1280, 720],
        "source_context": {"kind": "monitor", "source_id": "display-1"},
        "count": {"running": 4, "true": 2.1, "cards_seen": 24,
                  "decks_remaining": 5.5, "count_uncertain": True,
                  "per_rank": {}},
        "reading": {"count_uncertain": False},
        "card_groups": [{"seat_id": "hero", "is_hero": True,
                         "is_ambiguous": False, "cards": []}],
        "dealer_cards": [],
        "physical_advice": None,
        "error": None,
        "source_error": None,
    }


def _render_gui(snapshot):
    table = Mock()
    overlay = Mock()
    gui = SimpleNamespace(
        table=table,
        round_overlay=overlay,
        hud=overlay,
        mode_var=SimpleNamespace(get=lambda: APP_MODE_LABELS[INDIVIDUAL]),
        profile_var=SimpleNamespace(get=lambda: "Live"),
        monitor=SimpleNamespace(width=1280, height=720),
        _source_configured=True,
        _applied_mode_context=("Live", INDIVIDUAL, (1280, 720)),
        controller=SimpleNamespace(running=True, state="running"),
        _table_presentation=None,
        _last_table_signature=None,
        _selected_app_mode=lambda: INDIVIDUAL,
    )
    GUI._render_table(gui, snapshot)
    return gui, table, overlay


def test_main_table_receives_compact_hud_telemetry_from_snapshot():
    _gui, table, _overlay = _render_gui(_snapshot())

    table.update_telemetry.assert_called_once()
    telemetry = table.update_telemetry.call_args.args[0]
    assert telemetry["running"] == 4
    assert telemetry["true"] == pytest.approx(2.1)
    assert telemetry["cards_seen"] == 24
    assert telemetry["decks_remaining"] == pytest.approx(5.5)
    assert telemetry["count_uncertain"] is True
    assert telemetry["round_id"] == 6
    assert telemetry["mode"] == INDIVIDUAL
    assert telemetry["running_state"] == "Activo"
    assert isinstance(telemetry["advice"], str)


def test_mini_table_hud_shows_counts_progress_uncertainty_context_and_advice():
    mini = object.__new__(MiniTableOverlay)
    mini.win = SimpleNamespace(winfo_exists=lambda: True)
    mini.canvas = SimpleNamespace()
    mini._last_state = {
        "status": "ready", "message": "Mano lista", "round_id": 6,
        "mode": INDIVIDUAL, "players": [{"is_hero": True, "is_ambiguous": False,
            "cards": [{"rank": "10", "state": "confirmed"},
                      {"rank": "7", "state": "confirmed"}],
            "advice": "Mano 1: PEDIR"}], "advice_available": True,
        "dealer": {"cards": [{"rank": "Ace", "state": "confirmed"}]},
        "insurance": {"status": "ready", "text": "INSURANCE: NO"},
    }
    mini._metrics = {"running": 4, "true": 2.1, "count_uncertain": True,
                     "shoe_uncertainty": True, "decks_remaining": 5.5,
                     "cards_seen": 24}
    mini._running = True
    mini.editing = False
    mini.enabled = True
    mini.opacity = .85
    mini._active = False
    mini._drawn_cards = []
    texts = []
    rects = []
    mini.canvas.create_rectangle = lambda *a, **k: rects.append((a, k))
    mini.canvas.create_text = lambda *a, **k: texts.append(k.get("text")) or len(texts)
    mini.canvas.delete = Mock()
    mini.canvas.tag_bind = Mock()
    mini.win.attributes = Mock()
    mini._show_window = Mock()
    mini.win.withdraw = Mock()
    MiniTableOverlay._draw(mini)
    displayed = " | ".join(map(str, texts))
    assert "RC: +4" in displayed and "TC: +2.1" in displayed
    assert "5.5" in displayed and "24" in displayed
    assert "Ronda #6" in displayed and "Individual" in displayed
    assert "Lectura dudosa" in displayed
    assert "PEDIR" in displayed and "INSURANCE: NO" in displayed


class _Picker:
    def __init__(self):
        self.closed = False
        self.cancelled = False

    def winfo_exists(self):
        return not self.closed

    def _cancel(self):
        self.cancelled = True
        self.closed = True

    def destroy(self):
        self.closed = True


def _transition_gui(picker):
    table = Mock()
    overlay = Mock()
    controller = SimpleNamespace(
        running=True, configuration_busy=True, stop=Mock(),
        engine=SimpleNamespace(new_round=Mock(), reset_shoe=Mock(),
                               get_snapshot=lambda: None),
    )
    return SimpleNamespace(
        _card_correction_picker=picker,
        controller=controller,
        executor=SimpleNamespace(disarm=Mock()),
        _hide_marker=Mock(),
        start_btn=Mock(),
        set_status=Mock(),
        ui_actions=SimpleNamespace(pending=Mock(return_value=object()), blocked=Mock()),
        _set_configuration_controls=Mock(),
        table=table, round_overlay=overlay, _table_presentation=None,
        _last_table_signature=None, _table_metrics={},
        mode_var=SimpleNamespace(get=lambda: APP_MODE_LABELS[INDIVIDUAL], set=Mock()),
        profile_var=SimpleNamespace(get=lambda: "Live"),
        _selected_app_mode=lambda: INDIVIDUAL,
        _applied_mode_context=("Live", INDIVIDUAL, (1280, 720)),
        _source_configured=True,
        monitor=SimpleNamespace(width=1280, height=720),
    )


@pytest.mark.parametrize("transition", ["stop", "round", "shoe"])
def test_state_transition_closes_identity_picker(monkeypatch, transition):
    picker = _Picker()
    gui = _transition_gui(picker)
    engine = gui.controller.engine
    if transition == "stop":
        GUI._toggle_detection(gui)
    elif transition == "round":
        GUI._new_round(gui)
    else:
        monkeypatch.setattr("lib.interfaces.modern_gui.messagebox.askyesno", lambda *a: True)
        GUI._new_shoe(gui)
    assert picker.closed
    if transition == "round":
        engine.new_round.assert_called_once()
    elif transition == "shoe":
        engine.reset_shoe.assert_called_once()


@pytest.mark.parametrize("method", ["_select_mode", "_refresh_sources", "_select_profile"])
def test_configuration_change_invalidates_open_picker(method):
    picker = _Picker()
    gui = _transition_gui(picker)
    gui.controller.running = False
    gui.controller.configuration_busy = False
    gui._app_mode = INDIVIDUAL
    gui.source_kind = SimpleNamespace(get=lambda: "Monitor", set=Mock())
    gui.ui_actions = Mock(current=None)
    gui.monitor = None
    gui.profile_var = SimpleNamespace(get=lambda: "new profile")
    gui._profile_by_label = {"new profile": "new profile"}
    gui._refresh_profiles = Mock()
    # This shared helper is intentionally tested through each public transition.
    if method == "_select_mode":
        gui._selected_app_mode = Mock(return_value=INDIVIDUAL)
        GUI._select_mode(gui)
    elif method == "_refresh_sources":
        gui._source_kind_selection = "Monitor"
        gui._monitors = []
        class Combo:
            def __setitem__(self, key, value):
                setattr(self, key, value)

            set = Mock()

        gui.monitor_combo = Combo()
        gui.monitor_status = SimpleNamespace(config=Mock())
        for name in ("start_btn", "calibrate_btn", "ocr_btn", "controls_btn", "anchors_btn"):
            setattr(gui, name, Mock())
        from lib.interfaces import modern_gui as gui_module
        from unittest.mock import patch
        with patch.object(gui_module.screeninfo, "get_monitors", return_value=[]):
            GUI._refresh_sources(gui)
    else:
        from lib.logic import region_profiles
        from unittest.mock import patch
        gui.profile_var = SimpleNamespace(get=lambda: "new profile")
        gui._profile_by_label = {"new profile": "new profile"}
        gui._mode_context = None
        gui._refresh_profiles = Mock()
        with patch.object(region_profiles, "set_active", return_value=True):
            GUI._select_profile(gui)
    assert picker.closed


def test_worker_model_start_error_is_logged_and_leaves_detection_stopped():
    entered = threading.Event()
    logs = []

    class Engine:
        last_error = None
        capture = SimpleNamespace(close_local=lambda: None)

        def warm_up(self):
            raise ImportError("synthetic model backend unavailable")

        def publish_snapshot(self):
            entered.set()

    controller = DetectionController(log=logs.append)
    controller.engine = Engine()
    assert controller.start()
    assert entered.wait(2)
    deadline = time.monotonic() + 2
    while controller.configuration_busy and time.monotonic() < deadline:
        time.sleep(.005)
    assert controller.state == "error"
    assert not controller.running
    assert any("Model initialization failed" in line for line in logs)
    assert any("Traceback" in line for line in logs)


def test_worker_source_start_error_is_logged_and_leaves_detection_stopped():
    entered = threading.Event()
    logs = []

    class Engine:
        last_error = None
        current_phase = None
        provider = SimpleNamespace(backend_name="fake")
        capture = SimpleNamespace(close_local=lambda: None)

        def warm_up(self):
            return None

        def run_cycle(self):
            raise OSError("synthetic capture source unavailable")

        def health_check_and_refresh(self):
            return True

        def publish_snapshot(self):
            entered.set()

    controller = DetectionController(log=logs.append)
    controller.engine = Engine()
    assert controller.start()
    assert entered.wait(2)
    deadline = time.monotonic() + 2
    while controller.configuration_busy and time.monotonic() < deadline:
        time.sleep(.005)
    assert controller.state == "error"
    assert not controller.running
    assert any("Detection worker failed" in line for line in logs)
    assert any("synthetic capture source unavailable" in line for line in logs)


def test_invalid_configuration_clears_both_views_without_reusing_cards():
    old, _table, _overlay = _render_gui(_snapshot())
    table = old.table
    overlay = old.round_overlay
    old._source_configured = False
    old._table_presentation = None
    old._last_table_signature = None
    table.reset_mock()
    overlay.reset_mock()

    presentation = GUI._render_table(old, None)

    assert presentation["status"] == "needs_calibration"
    assert presentation["dealer"]["cards"] == []
    assert len(presentation["players"]) == 1
    assert presentation["players"][0]["cards"] == []
    assert table.update.call_args.args[0] is presentation
    assert overlay.update_from_presentation.call_args.args[0] is presentation


def test_start_is_rejected_when_live_calibration_is_not_valid():
    controller = SimpleNamespace(running=False, configuration_busy=False,
                                 start=Mock())
    gui = SimpleNamespace(
        controller=controller,
        monitor=SimpleNamespace(width=1280, height=720),
        _source_configured=False,
        set_status=Mock(),
        _confirm_monitor=Mock(return_value=False),
    )

    GUI._toggle_detection(gui)

    controller.start.assert_not_called()
    gui._confirm_monitor.assert_called_once()
