"""Official mode wiring with in-memory layouts and isolated object doubles."""
from threading import RLock
from types import SimpleNamespace, MethodType
from unittest.mock import Mock

import numpy as np
import pytest

from lib.interfaces import modern_gui as gui_module
from lib.interfaces.modern_gui import ModernBlackjackGUI as GUI, MODE_BY_LABEL
from lib.logic import engine as engine_module, region_profiles
from lib.logic.background import DetectionController
from lib.logic.card_layout import CardLayout
from lib.logic.card_memory import CardMemory
from lib.logic.round_observation import RoundObservation


def layout(mode="individual", resolution=(320, 180), reference=None):
    data = {"mode": mode, "resolution": list(resolution),
            "mine": [[20, 100], [100, 100], [100, 160], [20, 160]],
            "dealer": [[100, 20], [200, 20], [200, 60], [100, 60]]}
    if mode == "multiplayer":
        data["table"] = [[5, 85], [315, 85], [315, 175], [5, 175]]
    if reference:
        data["reference_source"] = reference
    return CardLayout.from_payload(data, resolution)


def variable(value):
    state = [value]
    return SimpleNamespace(get=lambda: state[0], set=lambda v: state.__setitem__(0, v))


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(region_profiles, "active_name", Mock(return_value="Profile"))
    monkeypatch.setattr(region_profiles, "layout_mode", Mock(return_value="individual"))
    monkeypatch.setattr(region_profiles, "has_card_layout", Mock(return_value=True))
    monkeypatch.setattr(region_profiles, "has_calibration", Mock(return_value=True))
    monkeypatch.setattr(region_profiles, "get_card_layout", Mock(side_effect=lambda res, mode, **kw: layout(mode, res)))
    monkeypatch.setattr(region_profiles, "get_source_context", Mock(return_value={
        "kind": "monitor", "source_id": "monitor", "width": 320, "height": 180}))
    monkeypatch.setattr(region_profiles, "set_active", Mock(return_value=True))


def fake_gui():
    monitor = SimpleNamespace(width=320, height=180)
    g = SimpleNamespace(controller=SimpleNamespace(configuration_busy=False, running=False,
            set_monitor=Mock(), start=Mock(), stop=Mock(), engine=SimpleNamespace(ocr_calibrated=True)),
        monitor=monitor, _monitors=[monitor], mode_var=variable("Individual"), _app_mode="individual",
        _mode_context=None, _source_configured=False, _preview_source=None, _region_editor=None,
        _source_review_required=False, _configuration_was_busy=False,
        source_kind=variable("Monitor"), profile_var=variable("Profile"),
        _profile_by_label={"Profile": "Profile", "Other": "Other"},
        ui_actions=Mock(current=None), log_manager=Mock(), set_status=Mock(),
        executor=Mock(), _hide_marker=Mock(), _refresh_profiles=Mock(),
        _store_source_reference=Mock(), _calibration_capture=Mock(),
        round_enabled=variable(False), _toggle_round_overlay=Mock())
    for name in ("start_btn", "calibrate_btn", "regions_btn", "ocr_btn", "controls_btn", "anchors_btn",
                 "mode_combo", "profile_combo", "source_combo", "refresh_sources_btn", "monitor_status"):
        setattr(g, name, Mock())
    g.monitor_combo = Mock()
    g.monitor_combo.current.return_value = 0
    for name in ("_configuration_busy", "_selected_app_mode", "_sync_mode_context", "_select_mode",
                 "_confirm_monitor", "_set_configuration_controls", "_sync_start_button"):
        setattr(g, name, MethodType(getattr(GUI, name), g))
    return g


def test_labels_are_exactly_two():
    assert MODE_BY_LABEL == {"Individual": "individual", "Multijugador": "multiplayer"}


def test_visible_selector_initializes_individual_before_profiles(monkeypatch):
    g = Mock()
    g._refresh_profiles = Mock()
    for name in ("Frame", "Label", "BooleanVar"):
        monkeypatch.setattr(gui_module.tk, name, Mock())
    monkeypatch.setattr(gui_module.tk, "StringVar", lambda value="": variable(value))
    combos = [Mock(), Mock()]
    constructor = Mock(side_effect=combos)
    monkeypatch.setattr(gui_module.ttk, "Combobox", constructor)
    monkeypatch.setattr(gui_module.ttk, "Button", Mock())
    monkeypatch.setattr(gui_module, "ToolTip", Mock())
    GUI._build_controls_section(g, object())
    assert g.mode_var.get() == "Individual"
    assert constructor.call_args_list[0].kwargs["values"] == ["Individual", "Multijugador"]
    assert constructor.call_args_list[0].kwargs["state"] == "readonly"
    assert g.mode_combo is combos[0] and g.profile_combo is combos[1]
    assert g._app_mode == "individual"


@pytest.mark.parametrize("label,mode", list(MODE_BY_LABEL.items()))
def test_selected_label(label, mode):
    g = fake_gui()
    g.mode_var.set(label)
    assert g._selected_app_mode() == mode


def test_bad_label():
    g = fake_gui()
    g.mode_var.set("seven_seats")
    with pytest.raises(ValueError, match="inválida"):
        g._selected_app_mode()


def test_saved_context_manual_choice_and_resolution(store):
    g = fake_gui()
    region_profiles.layout_mode.side_effect = ["multiplayer", "individual", "multiplayer"]
    g._sync_mode_context("Profile", g.monitor)
    assert g.mode_var.get() == "Multijugador"
    g.mode_var.set("Individual")
    g._sync_mode_context("Profile", g.monitor)
    assert g.mode_var.get() == "Individual"
    g._sync_mode_context("Other", g.monitor)
    assert g.mode_var.get() == "Individual"
    g.monitor.width = 640
    g._sync_mode_context("Other", g.monitor)
    assert g.mode_var.get() == "Multijugador"
    assert region_profiles.layout_mode.call_count == 3


@pytest.mark.parametrize("mode", [None, "individual", "multiplayer"])
@pytest.mark.parametrize("alive,stopping", [(False, False), (True, False), (True, True)])
def test_controller_delegation_and_lifecycle(mode, alive, stopping):
    c = object.__new__(DetectionController)
    c._lifecycle_lock = RLock()
    c._thread = SimpleNamespace(is_alive=lambda: alive)
    c._stop = SimpleNamespace(is_set=lambda: stopping)
    c.engine = SimpleNamespace(set_monitor=Mock())
    assert c.configuration_busy == alive
    if alive:
        with pytest.raises(RuntimeError, match="terminando"):
            c.set_monitor("source", mode=mode, profile="Named")
        c.engine.set_monitor.assert_not_called()
    else:
        c.set_monitor("source", mode=mode, profile="Named")
        c.engine.set_monitor.assert_called_once_with("source", mode=mode, profile="Named")


def test_controller_old_signature():
    c = object.__new__(DetectionController)
    c._lifecycle_lock, c._thread = RLock(), None
    c.engine = SimpleNamespace(set_monitor=Mock())
    c.set_monitor("source")
    c.engine.set_monitor.assert_called_once_with("source", mode=None, profile=None)


def minimal_engine(monkeypatch):
    e = SimpleNamespace(_lock=RLock(), capture=SimpleNamespace(resolution=(320, 180), set_monitor=Mock()),
        _preview_cache_lock=RLock(), _preview_detection_cache={"old": True}, _last_frame=object(),
        card_layout=None, card_registry=None, counter=SimpleNamespace(cards_seen=12, shoe_id="shoe"),
        round_number=7, _active_mode="old", _active_profile="Old", _unread_positions=set(),
        seats=[SimpleNamespace(cards=[{"name": "Ace"}], split=True)], dealer_card="Ace",
        _round_observation=RoundObservation(), _pending_extra={}, _pending_positions={},
        _prev_seat_thumbs={0: object()}, _prev_thumb=object(), _prev_dealer_thumb=object(),
        regions=[object()], phase_detector=Mock(), log=Mock(), publish_snapshot=Mock())
    monkeypatch.setattr(engine_module.ocr, "load_regions", Mock(return_value=None))
    e.card_registry = CardMemory(e.counter)
    e.card_registry.round_number = e.round_number
    e._sync_registry_cards = MethodType(engine_module.DetectionEngine._sync_registry_cards, e)
    return e


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_engine_applies_official_layout_and_revalidates_without_reset(store, monkeypatch, mode):
    e = minimal_engine(monkeypatch)
    region_profiles.get_source_context.return_value = None
    region_profiles.layout_mode.return_value = mode
    engine_module.DetectionEngine.set_monitor(e, SimpleNamespace(width=320, height=180))
    assert e._active_mode == mode and e._active_profile == "Profile"
    assert e.card_layout == layout(mode)
    assert (e.card_layout.table is None) == (mode == "individual")
    assert isinstance(e.card_registry, CardMemory) and e.card_registry.round_number == 7
    assert e.regions == [] and e._dealer_rect == (100, 20, 200, 60)
    assert e.counter.cards_seen == 12 and e.counter.shoe_id == "shoe"
    assert e.seats[0].cards == [] and e.dealer_card is None
    assert e._prev_thumb is None and e._prev_seat_thumbs == {}
    assert e._preview_detection_cache is None and e._last_frame is None
    e.publish_snapshot.assert_called_once()
    registry = e.card_registry
    engine_module.DetectionEngine.set_monitor(e, SimpleNamespace(width=320, height=180), mode=mode)
    assert e.card_registry is registry
    assert e.capture.set_monitor.call_count == 1


def test_explicit_profile_is_passed_to_all_geometry_reads(store, monkeypatch):
    e = minimal_engine(monkeypatch)
    region_profiles.get_source_context.return_value = None
    engine_module.DetectionEngine.set_monitor(e, SimpleNamespace(width=320, height=180),
                                            mode="multiplayer", profile="Other")
    region_profiles.get_card_layout.assert_called_once_with((320, 180), "multiplayer", profile="Other")
    region_profiles.get_source_context.assert_called_once_with((320, 180), profile="Other")
    assert e._active_profile == "Other" and e._active_mode == "multiplayer"
    region_profiles.set_active.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "corrupt", "mismatch", "resolution", "video", "source", "io", "seven_seats"])
def test_engine_failure_preserves_active_state(store, monkeypatch, failure):
    e = minimal_engine(monkeypatch)
    original = (e.card_layout, e.card_registry, e._active_mode, e._active_profile, e.counter.cards_seen)
    mode = "individual"
    if failure == "missing":
        region_profiles.get_card_layout.return_value = None
        region_profiles.get_card_layout.side_effect = None
    elif failure == "corrupt":
        region_profiles.get_card_layout.side_effect = ValueError("corrupt")
    elif failure in ("mismatch", "resolution", "video"):
        region_profiles.get_card_layout.side_effect = None
        region_profiles.get_card_layout.return_value = layout(
            "multiplayer" if failure == "mismatch" else "individual",
            (640, 360) if failure == "resolution" else (320, 180),
            {"kind": "video_frame"} if failure == "video" else None)
    elif failure == "source":
        region_profiles.get_source_context.return_value = {"kind": "window", "source_id": "other"}
    elif failure == "io":
        region_profiles.get_card_layout.side_effect = OSError("permission denied")
    elif failure == "seven_seats":
        mode = "seven_seats"
    with pytest.raises((ValueError, OSError)):
        engine_module.DetectionEngine.set_monitor(e, SimpleNamespace(width=320, height=180), mode=mode)
    assert (e.card_layout, e.card_registry, e._active_mode, e._active_profile, e.counter.cards_seen) == original
    e.capture.set_monitor.assert_not_called()
    e.publish_snapshot.assert_not_called()


def test_anchor_transform_keeps_official_geometry(monkeypatch):
    e = SimpleNamespace(card_layout=layout(), capture=SimpleNamespace(resolution=(320, 180)),
        _lock=RLock(), regions=[], _dealer_rect=(100, 20, 200, 60), phase_detector=Mock(),
        log=Mock(), publish_snapshot=Mock())
    fit = {"scale": 1.2, "dx": 10, "dy": 10, "matched": 3, "score": .95}
    for name, value in (("calibrated_resolution", (320, 180)), ("load_anchors", {"a": {}, "b": {}, "c": {}}),
                        ("solve", fit), ("transform_ocr", None)):
        monkeypatch.setattr(engine_module.anchors_mod, name, Mock(return_value=value))
    legacy = Mock(side_effect=AssertionError("must not load fixed seats"))
    monkeypatch.setattr(engine_module, "load_custom_regions", legacy)
    transform = Mock(side_effect=AssertionError("must not transform layout as fixed seats"))
    monkeypatch.setattr(engine_module.anchors_mod, "transform_regions", transform)
    monkeypatch.setattr(engine_module.ocr, "load_regions", Mock(return_value=None))
    monkeypatch.setattr(engine_module.phase, "load_controls", Mock(return_value={}))
    engine_module.DetectionEngine._resolve_anchors(e, np.zeros((180, 320, 3), np.uint8))
    assert e.card_layout == layout() and e.regions == [] and e._dealer_rect == (100, 20, 200, 60)
    legacy.assert_not_called()
    transform.assert_not_called()
    assert any("geometry kept" in str(c) for c in e.log.call_args_list)


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_confirm_passes_exact_mode_and_profile(store, mode):
    g = fake_gui()
    region_profiles.layout_mode.return_value = mode
    assert g._confirm_monitor()
    g.controller.set_monitor.assert_called_once_with(g.monitor, mode=mode, profile="Profile")
    assert g._source_configured
    g.start_btn.config.assert_called_with(state="normal")


@pytest.mark.parametrize("error", [None, ValueError("corrupt geometry"), OSError("disk permission denied")])
def test_missing_or_corrupt_or_inaccessible_profile_blocks_start(store, error):
    g = fake_gui()
    region_profiles.has_card_layout.return_value = False
    region_profiles.get_card_layout.side_effect = error
    region_profiles.get_card_layout.return_value = None
    if isinstance(error, OSError):
        region_profiles.has_card_layout.side_effect = error
    assert not g._confirm_monitor()
    assert not g._source_configured
    g.start_btn.config.assert_called_with(state="disabled")
    g.calibrate_btn.config.assert_called_with(state="normal")
    g.controller.set_monitor.assert_not_called()
    assert (str(error) if error else "Individual") in g.set_status.call_args.args[0]


@pytest.mark.parametrize("running", [True, False])
def test_all_configuration_paths_block_alive_worker(store, running):
    g = fake_gui()
    g.controller.configuration_busy = True
    g.controller.running = running
    g.mode_var.set("Multijugador")
    GUI._select_mode(g)
    assert g.mode_var.get() == "Individual"
    GUI._select_profile(g)
    GUI._refresh_sources(g)
    assert not g._confirm_monitor()
    GUI._calibrate_regions(g)
    g.controller.set_monitor.assert_not_called()
    region_profiles.set_active.assert_not_called()
    g._calibration_capture.assert_not_called()


def test_manual_mode_reapply_does_not_restore_saved_mode(store):
    g = fake_gui()
    assert g._confirm_monitor()
    g.mode_var.set("Multijugador")
    g._select_mode()
    assert g.mode_var.get() == "Multijugador"
    assert g.controller.set_monitor.call_args.kwargs["mode"] == "multiplayer"
    region_profiles.set_active.assert_not_called()


def test_manual_mode_missing_layout_invalidates_old_configuration(store):
    g = fake_gui()
    assert g._confirm_monitor()
    region_profiles.has_card_layout.return_value = False
    region_profiles.get_card_layout.side_effect = None
    region_profiles.get_card_layout.return_value = None
    g.mode_var.set("Multijugador")
    g._select_mode()
    assert g._app_mode == "multiplayer" and not g._source_configured
    g.start_btn.config.assert_called_with(state="disabled")
    assert g.controller.set_monitor.call_count == 1  # old application only


def test_incompatible_or_ambiguous_source_does_not_apply(store):
    g = fake_gui()
    region_profiles.get_source_context.return_value = {"kind": "window", "source_id": "other"}
    assert not g._confirm_monitor()
    assert g._source_review_required and not g._source_configured
    g.controller.set_monitor.assert_not_called()


def test_access_error_on_manual_mode_does_not_start_worker(store):
    g = fake_gui()
    region_profiles.layout_mode.side_effect = OSError("read blocked")
    g.mode_var.set("Multijugador")
    g._select_mode()
    assert not g._source_configured
    g.controller.start.assert_not_called()
    assert "read blocked" in g.set_status.call_args.args[0]


def test_profile_selection_does_not_claim_failed_application(store):
    g = fake_gui()
    g.profile_var.set("Other")
    region_profiles.active_name.return_value = "Other"
    region_profiles.layout_mode.return_value = "multiplayer"
    region_profiles.has_card_layout.return_value = False
    region_profiles.get_card_layout.side_effect = None
    region_profiles.get_card_layout.return_value = None
    GUI._select_profile(g)
    assert g.mode_var.get() == "Multijugador"
    assert not g._source_configured
    assert "aplicado al motor" not in str(g.set_status.call_args_list)


def test_profile_selection_restores_mode_before_application(store):
    g = fake_gui()
    g.profile_var.set("Other")
    region_profiles.active_name.return_value = "Other"
    region_profiles.layout_mode.return_value = "multiplayer"
    GUI._select_profile(g)
    assert g.mode_var.get() == "Multijugador" and g._source_configured
    g.controller.set_monitor.assert_called_once_with(g.monitor, mode="multiplayer", profile="Other")


def test_start_rechecks_layout_before_starting(store):
    g = fake_gui()
    g._source_configured = True
    region_profiles.has_card_layout.return_value = False
    region_profiles.get_card_layout.side_effect = None
    region_profiles.get_card_layout.return_value = None
    GUI._toggle_detection(g)
    g.controller.start.assert_not_called()
    assert not g._source_configured


def test_calibration_open_failure_does_not_declare_success(store, monkeypatch):
    g = fake_gui()
    capture = Mock(resolution=(320, 180))
    g._calibration_capture.return_value = capture
    monkeypatch.setattr("lib.interfaces.region_editor.RegionEditor", Mock(side_effect=ValueError("invalid layout")))
    error = Mock()
    monkeypatch.setattr(gui_module.messagebox, "showerror", error)
    GUI._calibrate_regions(g)
    error.assert_called_once()
    g.controller.set_monitor.assert_not_called()
    capture.close_local.assert_called_once()
    assert "Perfil aplicado" not in str(g.set_status.call_args_list)


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("preview,apply", [(False, True), (False, False), (True, False)])
def test_calibration_uses_selection_and_reports_real_application(store, monkeypatch, mode, preview, apply):
    g = fake_gui()
    g.mode_var.set(gui_module.APP_MODE_LABELS[mode])
    capture = Mock(resolution=(320, 180))
    g._calibration_capture.return_value = capture
    if preview:
        g._preview_source = capture
    region_profiles.layout_mode.return_value = mode
    g._confirm_monitor = Mock(return_value=apply)
    editor = Mock(_shown_logged=True)
    constructor = Mock(return_value=editor)
    monkeypatch.setattr("lib.interfaces.region_editor.RegionEditor", constructor)
    GUI._calibrate_regions(g)
    assert constructor.call_args.kwargs["mode"] == mode
    assert constructor.call_args.args[1] is capture
    constructor.call_args.kwargs["on_save"]()
    g._refresh_profiles.assert_called_once()
    if preview:
        g._confirm_monitor.assert_not_called()
        assert "no aplicadas" in g.set_status.call_args.args[0]
    elif apply:
        g._confirm_monitor.assert_called_once()
        assert "Perfil aplicado" in g.set_status.call_args.args[0]
    else:
        assert "Perfil aplicado" not in str(g.set_status.call_args_list)
        g.start_btn.config.assert_called_with(state="disabled")
    capture.close_local.assert_called_once()


def test_worker_finishes_restores_only_valid_controls(store):
    g = fake_gui()
    g._configuration_was_busy = True
    region_profiles.has_card_layout.return_value = False
    region_profiles.get_card_layout.side_effect = None
    region_profiles.get_card_layout.return_value = None
    g._sync_start_button()
    g.start_btn.config.assert_called_with(state="disabled")
    g.calibrate_btn.config.assert_called_with(state="normal")
    g.ocr_btn.config.assert_called_with(state="disabled")
    g.mode_combo.config.assert_called_with(state="readonly")


def test_stop_does_not_unlock_configuration_until_thread_ends():
    g = fake_gui()
    g.controller.running = True
    g.controller.configuration_busy = True
    GUI._toggle_detection(g)
    g.controller.stop.assert_called_once()
    g.mode_combo.config.assert_called_with(state="disabled")
    g.start_btn.config.assert_called_with(state="disabled")
    g.controller.running = False
    g._sync_start_button()
    g.mode_combo.config.assert_called_with(state="disabled")


@pytest.mark.parametrize("configured,snapshot_mode,rendered", [
    (True, "individual", True), (True, "multiplayer", False), (False, "individual", False),
])
def test_poll_never_presents_snapshot_from_failed_or_different_configuration(configured, snapshot_mode, rendered):
    g = fake_gui()
    g._source_configured = configured
    g._applied_mode_context = ("Profile", "individual", (320, 180))
    snapshot = {"seq": 9, "source_profile": "Profile", "source_mode": snapshot_mode,
                "source_resolution": [320, 180]}
    g.controller.engine.get_snapshot = Mock(return_value=snapshot)
    g._last_seq, g.round_overlay, g._detection_action = -1, None, None
    g._render, g._render_executor, g.after = Mock(), Mock(), Mock()
    g._poll_snapshot = Mock()
    GUI._poll_snapshot(g)
    assert g._render.call_count == int(rendered)
    g.executor.step.assert_called_once_with(snapshot if rendered else None)
    g.after.assert_called_once()
