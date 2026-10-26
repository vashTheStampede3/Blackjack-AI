"""Mini Mesa state, lifecycle and capture guards without windows or workers."""
from copy import deepcopy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.interfaces import modern_gui as gui_module, round_overlay as overlay_module
from lib.interfaces.modern_gui import ModernBlackjackGUI as GUI
from lib.interfaces.round_overlay import MiniTableOverlay as Overlay, overlay_visibility
from lib.interfaces.capture_guard import CaptureGuard
from tests.test_gui_sections_unit import Variable, Widget, sidebar


@pytest.fixture(autouse=True)
def isolated_ui_settings(monkeypatch):
    monkeypatch.setattr(overlay_module.constants, "UI", deepcopy(overlay_module.constants.UI))


def current_state():
    return {"source_profile": "Profile", "source_mode": "individual", "source_resolution": [320, 180],
            "round": 8, "shoe_id": "shoe", "seq": 3,
            "reading": {"present": True, "inferred_at": 10, "dealer_ok": True},
            "card_groups": [{"seat_id": "J1", "is_hero": True,
                             "cards": [{"card_id": "shoe:1", "rank": "8"}]}],
            "dealer_cards": [{"card_id": "shoe:2", "rank": "10"}],
            "seats": [{"mine": True, "cards": ["8", "8"], "optimal": "Split"}]}


@pytest.mark.parametrize("state,running,editing,enabled,expected", [
    (None, False, False, False, (False, "")),
    ({}, True, True, False, (False, "")),
    (None, False, False, True, (True, "DETECCIÓN DETENIDA")),
    (None, True, False, True, (True, "ESPERANDO CARTAS")),
    ({}, True, False, True, (True, "ESPERANDO CARTAS")),
    ({"reading": {"waiting_for_deal": True}}, True, False, True, (True, "ESPERANDO CARTAS")),
    ({"reading": {"table_clean_confirmed": True}}, True, False, True, (True, "ESPERANDO CARTAS")),
    ({"reading": {"clean_table_confirmed": True}}, True, False, True, (True, "ESPERANDO CARTAS")),
    ({"card_groups": [{"cards": [{"rank": "8"}]}]}, True, False, True, (True, "")),
    ({"dealer_cards": [{"rank": "10"}]}, True, False, True, (True, "")),
    ({"error": "capture failed"}, True, False, True, (True, "ERROR DE DETECCIÓN")),
    ({"source_error": "size changed"}, True, False, True, (True, "ERROR DE DETECCIÓN")),
    ({"error": "failed"}, False, False, True, (True, "DETECCIÓN DETENIDA")),
    ({"error": "failed"}, False, True, True, (True, "AJUSTE DE POSICIÓN")),
])
def test_visibility_priority_and_notices(state, running, editing, enabled, expected):
    original = deepcopy(state)
    assert overlay_visibility(state, running, editing, enabled) == expected
    assert state == original


@pytest.mark.parametrize("field", ["count_uncertain", "count_pending", "stale", "ambiguous"])
def test_reading_uncertainty_precedes_waiting(field):
    state = {"reading": {field: True, "waiting_for_deal": True}}
    assert overlay_visibility(state, True) == (True, "LECTURA INCIERTA · SIN CONSEJO")


def test_count_and_group_uncertainty():
    for state in ({"count": {"count_uncertain": True}}, {"card_groups": [{"is_ambiguous": True}]}):
        assert overlay_visibility(state, True) == (True, "LECTURA INCIERTA · SIN CONSEJO")


@pytest.mark.parametrize("phase", ["waiting", "betting_open"])
@pytest.mark.parametrize("container", ["reading", "phase"])
def test_waiting_phase_suppresses_residual_advice(phase, container):
    state = current_state()
    state[container] = {"phase": phase}
    assert overlay_visibility(state, True) == (True, "ESPERANDO CARTAS")
    assert Overlay._advice_text(state) == "Esperando cartas"


@pytest.mark.parametrize("case,expected", [("stopped", "Detección detenida"), ("empty", "Esperando cartas"),
    ("pending", "SIN CONSEJO"), ("ambiguous", "SIN CONSEJO"), ("error", "SIN CONSEJO"),
    ("no_reading", "Esperando cartas"), ("no_hand", "Esperando cartas"),
    ("no_dealer", "Esperando cartas"), ("valid", "Split")])
def test_advice_needs_current_reading_and_owned_hand(case, expected):
    state = current_state()
    if case == "empty":
        state = {"seats": state["seats"]}
    elif case == "pending":
        state["reading"]["count_pending"] = True
    elif case == "ambiguous":
        state["card_groups"][0]["is_ambiguous"] = True
    elif case == "error":
        state["error"] = "failed"
    elif case == "no_reading":
        state["reading"] = {}
    elif case == "no_hand":
        state["seats"][0]["cards"] = []
    elif case == "no_dealer":
        state["reading"]["dealer_ok"] = False
    original = deepcopy(state)
    assert expected in Overlay._advice_text(state, running=case != "stopped")
    assert state == original


def fake_overlay(on_close=None):
    o = object.__new__(Overlay)
    alive = [True]
    o.win = Mock()
    o.win.winfo_exists.side_effect = lambda: alive[0]
    o.win.destroy.side_effect = lambda: alive.__setitem__(0, False)
    o.win.winfo_x.return_value, o.win.winfo_y.return_value = 30, 40
    o.canvas = Mock()
    o.enabled, o._capture_hidden, o._active, o.editing = True, False, True, False
    o.hwnd, o.opacity, o._topmost_job = None, .8, "job"
    o._last_state, o._running, o._notice = {}, False, ""
    o._selected_id = o._selected_round = o._selected_ref = o._selected_suit = o._selected_expected_name = None
    o._text, o._rect, o._draw_card, o._draw_group, o._draw_picker = Mock(), Mock(), Mock(), Mock(), Mock()
    o.on_close, o.ensure_on_screen = on_close, Mock()
    return o


@pytest.mark.parametrize("running", [False, True])
def test_none_snapshot_clears_obsolete_selections_and_state(running):
    o = fake_overlay()
    o._last_state = current_state()
    o._selected_id, o._selected_round = "shoe:1", 8
    o._selected_ref = {"card_id": "shoe:1", "shoe_id": "shoe"}
    o._selected_suit, o._selected_expected_name = "H", "8"
    o.update_from_snapshot(None, running=running)
    assert o._last_state == {} and o._running is running
    assert all(getattr(o, field) is None for field in (
        "_selected_id", "_selected_round", "_selected_ref", "_selected_suit", "_selected_expected_name"))
    assert o._active
    o._draw_group.assert_not_called()
    o._draw_card.assert_not_called()


@pytest.mark.parametrize("changed", ["round", "shoe", "card"])
def test_snapshot_updates_preserve_stale_reference_protections(changed):
    o = fake_overlay()
    state = current_state()
    o._selected_id, o._selected_round = "shoe:1", 8
    o._selected_ref = {"card_id": "shoe:1", "shoe_id": "shoe"}
    if changed == "round":
        state["round"] = 9
    elif changed == "shoe":
        state["shoe_id"] = "new-shoe"
    else:
        state["card_groups"] = []
    o.update_from_snapshot(state, running=True)
    assert o._selected_id is None and o._selected_ref is None
    o._draw_picker.assert_not_called()


@pytest.mark.parametrize("case", ["stopped", "waiting", "error", "uncertain", "active"])
def test_draw_keeps_window_but_only_current_observations(case):
    o = fake_overlay()
    state = current_state()
    if case == "waiting":
        state["reading"]["waiting_for_deal"] = True
    elif case == "error":
        state["error"] = "failed"
    elif case == "uncertain":
        state["reading"]["stale"] = True
    before = deepcopy(state)
    o.update_from_snapshot(state, running=case != "stopped")
    assert state == before and o._active and o.enabled
    o.win.deiconify.assert_called()
    assert o._draw_group.call_count == int(case in ("uncertain", "active"))
    assert o._draw_card.call_count == int(case in ("uncertain", "active"))
    text = str(o._text.call_args_list)
    if case == "stopped":
        assert "Detección detenida" in text and "Split" not in text
    elif case in ("uncertain", "error"):
        assert "SIN CONSEJO" in text and "Split" not in text
    elif case == "waiting":
        assert "Esperando cartas" in text and "Split" not in text
    else:
        assert "Split" in text


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("alive", [False, True])
def test_capture_hiding_is_temporary_and_never_revives_closed_window(enabled, active, alive):
    o = fake_overlay()
    o.enabled, o._active = enabled, active
    o.win.winfo_exists.side_effect = None
    o.win.winfo_exists.return_value = alive
    win = o.win
    o.set_capture_hidden(True)
    assert o.enabled is enabled and o._capture_hidden
    assert win.withdraw.call_count == int(alive)
    o.set_capture_hidden(False)
    assert o.win is win and o.enabled is enabled and not o._capture_hidden
    assert win.deiconify.call_count == int(enabled and active and alive)
    win.destroy.assert_not_called()


@pytest.mark.parametrize("outcome", ["normal", "error", "closed"])
def test_existing_capture_guard_restores_safely_without_real_capture(outcome):
    o = fake_overlay()
    root = Mock()
    guard = CaptureGuard(root, lambda: o)

    def operation():
        with guard.capture():
            assert o._capture_hidden and o.enabled
            o.win.withdraw.assert_called_once()
            if outcome == "closed":
                o.close()
            if outcome == "error":
                raise RuntimeError("synthetic capture failure")

    if outcome == "error":
        with pytest.raises(RuntimeError, match="synthetic capture failure"):
            operation()
    else:
        operation()
    assert not o._capture_hidden
    assert o.win.deiconify.call_count == int(outcome != "closed")
    assert o.enabled == (outcome != "closed")
    guard.close()


@pytest.mark.parametrize("method", ["_show_window", "_show_noactivate"])
@pytest.mark.parametrize("blocked", ["capture", "disabled", "inactive", "destroyed"])
def test_native_show_paths_respect_all_guards(monkeypatch, method, blocked):
    o = fake_overlay()
    o.hwnd = 123
    native = Mock()
    monkeypatch.setattr(overlay_module.ctypes, "windll", SimpleNamespace(user32=native), raising=False)
    if blocked == "capture":
        o._capture_hidden = True
    elif blocked == "disabled":
        o.enabled = False
    elif blocked == "inactive":
        o._active = False
    else:
        o.win.winfo_exists.side_effect = None
        o.win.winfo_exists.return_value = False
    getattr(o, method)()
    assert native.mock_calls == []
    o.win.deiconify.assert_not_called()


@pytest.mark.parametrize("method", ["_show_window", "_show_noactivate"])
def test_native_show_paths_keep_noactivate_calls(monkeypatch, method):
    o = fake_overlay()
    o.hwnd = 123
    native = Mock()
    monkeypatch.setattr(overlay_module.ctypes, "windll", SimpleNamespace(user32=native), raising=False)
    getattr(o, method)()
    native.ShowWindow.assert_called_once_with(123, 4)
    native.SetWindowPos.assert_called_once()
    assert native.SetWindowPos.call_args.args[-1] == 0x13
    o.win.deiconify.assert_not_called()


def fake_gui():
    g = SimpleNamespace(round_enabled=Variable(False), round_overlay=None, hud=None, hud_nav_btn=Mock(),
        _source_configured=True, _applied_mode_context=("Profile", "individual", (320, 180)),
        controller=SimpleNamespace(running=False, engine=SimpleNamespace(
            get_snapshot=Mock(return_value=current_state()), persist_settings_async=Mock())),
        _correct_overlay_card=Mock(), set_status=Mock())
    for method in ("_toggle_round_overlay", "_toggle_hud", "_on_hud_closed"):
        setattr(g, method, MethodType(getattr(GUI, method), g))
    return g


@pytest.mark.parametrize("mismatch", [None, "source_profile", "source_mode", "source_resolution", "unconfigured"])
def test_activation_and_snapshot_filter_share_provenance(monkeypatch, mismatch):
    g = fake_gui()
    snapshot = g.controller.engine.get_snapshot.return_value
    if mismatch == "unconfigured":
        g._source_configured = False
    elif mismatch:
        snapshot[mismatch] = [640, 360] if mismatch == "source_resolution" else "Other"
    before = deepcopy(snapshot)
    o = fake_overlay()
    o.update_from_snapshot = Mock()
    factory = Mock(return_value=o)
    monkeypatch.setattr(overlay_module, "MiniTableOverlay", factory)
    g._toggle_hud()
    o.update_from_snapshot.assert_called_once_with(snapshot if mismatch is None else None, False)
    assert g.round_overlay is g.hud is o
    assert snapshot == before
    assert snapshot["round"] == 8 and snapshot["shoe_id"] == "shoe"
    g.controller.engine.persist_settings_async.assert_not_called()


def test_toggle_single_instance_close_x_poll_and_reopen(monkeypatch):
    g = fake_gui()
    instances = []

    def make(parent, on_correct_card, on_close):
        o = fake_overlay(on_close)
        instances.append(o)
        return o

    factory = Mock(side_effect=make)
    monkeypatch.setattr(overlay_module, "MiniTableOverlay", factory)
    g._toggle_hud()
    g._toggle_round_overlay()
    assert len(instances) == 1 and g.round_enabled.get()
    g.hud_nav_btn.config.assert_called_with(fg=gui_module.C["accent"])
    o = g.round_overlay
    o._close_by_user()
    assert not o.enabled and not o.win.winfo_exists()
    assert not g.round_enabled.get() and g.round_overlay is None and g.hud is None
    g.hud_nav_btn.config.assert_called_with(fg=gui_module.C["text_primary"])
    g._last_seq, g._detection_action = -1, None
    g._render, g._render_executor, g._sync_start_button, g.after, g._poll_snapshot = Mock(), Mock(), Mock(), Mock(), Mock()
    g.log_manager = Mock()
    GUI._poll_snapshot(g)
    assert len(instances) == 1 and g.round_overlay is None
    g._toggle_hud()
    assert len(instances) == 2 and g.round_overlay is g.hud is instances[1]
    g._toggle_hud()
    assert not g.round_enabled.get() and g.round_overlay is None
    assert not instances[1].win.winfo_exists()


def test_only_navigation_activates_overlay_and_options_do_not(sidebar, monkeypatch):
    g, widgets, _ = sidebar
    assert not any(w.options.get("text") == "Cartel de ronda" for w in widgets)
    settings = [w for w in widgets if w.options.get("text") == "Ajustes Mini Mesa"]
    assert len(settings) == 1 and settings[0].options["command"] is g._round_overlay_options
    g.round_enabled.set(False)
    g.round_overlay, g._round_options = None, None
    g.controller.engine.persist_settings_async = Mock()
    top = Mock()
    top.children = []
    monkeypatch.setattr(gui_module.tk, "Toplevel", Mock(return_value=top))
    monkeypatch.setattr(gui_module.tk, "DoubleVar", Variable)
    scales = []

    def scale(parent, **options):
        w = Widget(parent, **options)
        scales.append(w)
        return w

    monkeypatch.setattr(gui_module.tk, "Scale", scale)
    forbidden = Mock(side_effect=AssertionError("options must not create Mini Mesa"))
    monkeypatch.setattr(overlay_module, "MiniTableOverlay", forbidden)
    GUI._round_overlay_options(g)
    assert not any(w.options.get("text") == "Mostrar cartel" for w in widgets)
    position = next(w for w in widgets if "Ajustar posici" in w.options.get("text", ""))
    position.options["variable"].set(True)
    position.options["command"]()
    assert not position.options["variable"].get()
    assert not g.round_enabled.get() and g.round_overlay is None
    assert "botón superior" in g.set_status.call_args.args[0]
    scales[0].options["command"](65)
    assert not g.round_enabled.get()
    forbidden.assert_not_called()


def test_options_preserve_instance_activation_and_save_position(sidebar, monkeypatch):
    g, widgets, _ = sidebar
    o = fake_overlay()
    g.round_overlay, g.hud, g._round_options = o, o, None
    g.round_enabled.set(True)
    top = Mock()
    top.children = []
    monkeypatch.setattr(gui_module.tk, "Toplevel", Mock(return_value=top))
    monkeypatch.setattr(gui_module.tk, "DoubleVar", Variable)
    scale = Mock()
    monkeypatch.setattr(gui_module.tk, "Scale", scale)
    factory = Mock(side_effect=AssertionError("must not create another Mini Mesa"))
    monkeypatch.setattr(overlay_module, "MiniTableOverlay", factory)
    GUI._round_overlay_options(g)
    scale.call_args.kwargs["command"](60)
    assert o.opacity == .6 and g.round_enabled.get()
    position = next(w for w in widgets if "Ajustar posici" in w.options.get("text", ""))
    position.options["variable"].set(True)
    position.options["command"]()
    assert o.editing and g.round_overlay is o
    finish = next(w for w in widgets if w.options.get("text") == "Bloquear y guardar")
    finish.options["command"]()
    assert not o.editing and g.round_enabled.get() and g.round_overlay is g.hud is o
    assert gui_module.constants.UI["round_x"] == 30 and gui_module.constants.UI["round_y"] == 40
    factory.assert_not_called()
    top.destroy.assert_called_once()
