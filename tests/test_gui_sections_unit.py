"""Sidebar builders and updates with concrete widget doubles, without Tcl/Tk."""
from copy import deepcopy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.interfaces import modern_gui as gui_module
from lib.interfaces.modern_gui import ModernBlackjackGUI as GUI
from lib.logic.counting import COUNTER_KEYS


class Variable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    def __init__(self, master=None, **options):
        self.master = master
        self.options = dict(options)
        self.children = []
        self.bindings = {}
        self.mapped = False
        self.pack_calls = []
        self.forget_calls = 0
        self.item_options = {}
        self.bbox_value = (0, 0, 320, 600)
        self.yview_moveto = Mock()
        self.yview_scroll = Mock()
        if master is not None and hasattr(master, "children"):
            master.children.append(self)

    def pack(self, **options):
        self.pack_calls.append(options)
        self.mapped = True

    def pack_forget(self):
        self.forget_calls += 1
        self.mapped = False

    def grid(self, **options):
        self.mapped = True

    def pack_propagate(self, enabled):
        self.options["pack_propagate"] = enabled

    def grid_remove(self):
        raise AssertionError("Sidebar containers use pack, not grid_remove")

    def destroy(self):
        raise AssertionError("Hidden sections must retain their widgets")

    def config(self, **options):
        self.options.update(options)

    configure = config

    def cget(self, key):
        return self.options.get(key)

    def __getitem__(self, key):
        return self.options[key]

    def __setitem__(self, key, value):
        self.options[key] = value

    def bind(self, event, callback, **options):
        self.bindings[event] = callback

    def winfo_ismapped(self):
        parent_mapped = (self.master.winfo_ismapped() if isinstance(self.master, Widget)
                         else getattr(self.master, "mapped", True))
        return self.mapped and parent_mapped

    def set(self, value):
        var = self.options.get("textvariable")
        if var is not None:
            var.set(value)

    def current(self, index):
        self.set(self.options["values"][index])

    def yview(self, *args):
        pass

    def bbox(self, tag):
        assert tag == "all"
        return self.bbox_value

    def winfo_height(self):
        return 400

    def create_window(self, position, *, window, anchor):
        window.mapped = True
        return 1

    def itemconfigure(self, item, **options):
        self.item_options[item] = options


@pytest.fixture
def sidebar(monkeypatch):
    widgets = []

    def widget(master=None, **options):
        result = Widget(master, **options)
        widgets.append(result)
        return result

    for name in ("Frame", "Canvas", "Label", "Button", "Checkbutton", "Entry"):
        monkeypatch.setattr(gui_module.tk, name, widget)
    for name in ("Combobox", "Button", "Scrollbar"):
        monkeypatch.setattr(gui_module.ttk, name, widget)
    monkeypatch.setattr(gui_module.tk, "StringVar", Variable)
    monkeypatch.setattr(gui_module.tk, "BooleanVar", Variable)
    monkeypatch.setattr(gui_module, "attach_numeric_entry", Mock())
    tooltips = Mock()
    monkeypatch.setattr(gui_module, "ToolTip", tooltips)
    monkeypatch.setattr(gui_module.scaling, "px", lambda value: value)
    monkeypatch.setattr(gui_module.screeninfo, "get_monitors", lambda: [
        SimpleNamespace(width=1920, height=1080, is_primary=True)])
    g = SimpleNamespace(children=[], mapped=True, monitor=None, _source_configured=False,
        controller=SimpleNamespace(running=False, configuration_busy=False, engine=Mock()),
        executor=SimpleNamespace(mode="off", allowed=False, set_mode=Mock()),
        _refresh_profiles=Mock(), _scrollbar_style=Mock(return_value="test"),
        bind_all=Mock(), focus_get=lambda: None, set_status=Mock(),
        table=SimpleNamespace(remember_snapshot=Mock(), update=Mock()), hud=None,
        metrics_var=Variable(), _render_executor=Mock())
    for name in ("_calibrate_regions", "_refresh_sources", "_confirm_monitor", "_select_mode", "_toggle_detection",
                 "_select_profile", "_new_round", "_new_shoe", "_preview_regions",
                 "_calibrate_ocr", "_calibrate_controls", "_calibrate_anchors", "_toggle_arm",
                 "_set_executor_mode", "_adjust_counter", "_set_bankroll", "_set_bet_placed",
                 "_set_side_bet_stake", "_toggle_round_overlay", "_round_overlay_options"):
        setattr(g, name, Mock())
    for name in ("_section", "_button", "_build_monitor_section", "_build_controls_section",
                 "_build_executor_section", "_build_counters_section", "_build_game_info_section",
                 "_build_sidebets_section", "_build_hidden_sidebar_sections", "_toggle_advanced_tools",
                 "_set_configuration_controls", "_sync_money_entries", "_entry_editing"):
        setattr(g, name, MethodType(getattr(GUI, name), g))
    GUI._build_left_panel(g)
    for name in ("guardrail_banner", "anchor_badge", "reshuffle_badge", "paytable_warning"):
        setattr(g, name, Widget())
    return g, widgets, tooltips


def test_one_calibration_button_and_original_callbacks(sidebar):
    g, widgets, tooltips = sidebar
    calibration = [w for w in widgets if "Calibrate Regions" in w.options.get("text", "")]
    assert calibration == [g.calibrate_btn]
    assert "calibration_action_btn" not in vars(g)
    assert g.calibrate_btn.options["command"] is g._calibrate_regions
    assert g.calibrate_btn.options["state"] == "disabled"
    assert g.calibrate_btn.winfo_ismapped()
    g.calibrate_btn.options["command"]()
    g._calibrate_regions.assert_called_once_with()
    hints = {id(call.args[0]): call.args[1] for call in tooltips.call_args_list}
    assert "Individual" in hints[id(g.calibrate_btn)]
    assert "Multijugador" in hints[id(g.calibrate_btn)]
    assert "modo activo" in hints[id(g.regions_btn)]
    for w in widgets:
        if any(text in w.options.get("text", "") for text in ("Calibrate Regions", "Preview Regions", "New Round")):
            hint = hints[id(w)].lower()
            assert "seven" not in hint and "siete" not in hint and "seat" not in hint


@pytest.mark.parametrize("source,configured,busy", [
    (False, False, False), (True, False, False), (True, True, False),
    (True, True, True), (True, False, True),
])
def test_calibration_availability_is_preserved(sidebar, source, configured, busy):
    g, _, _ = sidebar
    g.monitor = object() if source else None
    g._source_configured = configured
    g.controller.configuration_busy = busy
    GUI._set_configuration_controls(g, busy)
    assert g.calibrate_btn.options["state"] == ("normal" if source and not busy else "disabled")
    g._calibrate_regions.assert_not_called()


@pytest.mark.parametrize("key,title", [
    ("cards_seen", "Cards Seen (this shoe)"), ("game_info", "Game Info"),
    ("side_bets", "Side Bets (EV per unit · € stake)"),
])
def test_secondary_containers_hidden_including_titles_and_children(sidebar, key, title):
    g, widgets, _ = sidebar
    assert set(g._hidden_sidebar_sections) == {"cards_seen", "game_info", "side_bets"}
    section = g._hidden_sidebar_sections[key]
    assert section.forget_calls == 1 and len(section.pack_calls) == 1
    assert not section.mapped and not section.winfo_ismapped()
    titles = [w for w in widgets if w.options.get("text") == title]
    assert len(titles) == 1 and titles[0].master is section
    assert not titles[0].winfo_ismapped()
    assert section.children  # Still allocated; nothing was destroyed.
    assert all(not child.winfo_ismapped() for child in section.children)


@pytest.mark.parametrize("builder", ["_build_counters_section", "_build_game_info_section", "_build_sidebets_section"])
def test_original_builders_return_their_container(sidebar, builder):
    g, _, _ = sidebar
    original = dict(g._hidden_sidebar_sections)
    # Use a separate owner so this assertion does not create duplicate sections in g.
    owner = SimpleNamespace(**vars(g))
    owner._section = MethodType(GUI._section, owner)
    parent = Widget()
    container = getattr(GUI, builder)(owner, parent)
    assert container.master is parent and container.mapped
    assert len(parent.children) == 1
    assert g._hidden_sidebar_sections == original


@pytest.mark.parametrize("missing", ["cards_seen", "game_info", "side_bets"])
def test_missing_container_is_explicit_error(missing):
    names = {"cards_seen": "_build_counters_section", "game_info": "_build_game_info_section",
             "side_bets": "_build_sidebets_section"}
    g = SimpleNamespace(**{method: Mock(return_value=None if key == missing else Widget())
                           for key, method in names.items()})
    with pytest.raises(RuntimeError, match=missing):
        GUI._build_hidden_sidebar_sections(g, object())


def test_hidden_variables_preserve_initial_values_and_remain_writable(sidebar):
    g, _, _ = sidebar
    assert set(g.counter_vars) == set(COUNTER_KEYS)
    assert set(g.info_vars) == {"round", "phase", "running", "true", "decks", "seen", "bet", "behind", "pnl"}
    assert set(g.sidebet_rows) == set(gui_module.constants.SIDE_BETS)
    assert set(g.sidebet_stake_vars) == set(gui_module.constants.SIDE_BETS)
    assert g.bankroll_entry.options["textvariable"] is g.bankroll_var
    assert g.bet_entry.options["textvariable"] is g.bet_placed_var
    assert float(g.bankroll_var.get()) == g._synced_bankroll == gui_module.constants.BETTING["bankroll"]
    assert float(g.bet_placed_var.get()) == g._synced_bet == float(gui_module.constants.BASE_BET)
    for key, cfg in gui_module.constants.SIDE_BETS.items():
        assert float(g.sidebet_stake_vars[key].get()) == float(cfg.get("stake") or 0)
    for group in (g.counter_vars, g.info_vars, g.sidebet_stake_vars):
        for var in group.values():
            var.set("42")
            assert var.get() == "42"
    for _, var, _ in g.sidebet_rows.values():
        var.set("updated")
        assert var.get() == "updated"


def test_snapshot_updates_hidden_data_without_repacking_containers_or_business_callbacks(sidebar):
    g, _, _ = sidebar
    bets = deepcopy(gui_module.constants.SIDE_BETS)
    bankroll = deepcopy(gui_module.constants.BETTING)
    snap = {"count": {"per_rank": {key: i+1 for i, key in enumerate(COUNTER_KEYS)},
                      "true": 1.5, "running": 3, "decks_remaining": 5.2, "cards_seen": 18},
            "round": 4, "bet": "Bet hint", "error": None, "metrics": {},
            "bankroll": g._synced_bankroll+10, "bet_placed": g._synced_bet+1,
            "side_bets": [{"key": key, "ev": .02, "stake_suggested": 1}
                          for key in g.sidebet_rows]}
    original = deepcopy(snap)
    for _ in range(2):
        GUI._render(g, snap)
    for key, var in g.counter_vars.items():
        assert var.get() == str(snap["count"]["per_rank"][key])
    assert g.info_vars["round"].get() == "4"
    assert g.info_vars["running"].get() == "+3"
    assert g.info_vars["true"].get() == "+1.5"
    assert g.info_vars["decks"].get() == "5.2"
    assert g.info_vars["seen"].get() == "18"
    assert g.info_vars["bet"].get() == "Bet hint"
    assert float(g.bankroll_var.get()) == snap["bankroll"]
    assert float(g.bet_placed_var.get()) == snap["bet_placed"]
    assert all("+2.00%" in var.get() for _, var, _ in g.sidebet_rows.values())
    assert snap == original
    assert gui_module.constants.SIDE_BETS == bets and gui_module.constants.BETTING == bankroll
    for section in g._hidden_sidebar_sections.values():
        assert len(section.pack_calls) == 1 and section.forget_calls == 1
        assert not section.winfo_ismapped()
    for callback in (g._adjust_counter, g._set_bankroll, g._set_bet_placed, g._set_side_bet_stake):
        callback.assert_not_called()
    assert g.controller.running is False and g.controller.configuration_busy is False
    assert g.controller.engine.mock_calls == []


def test_primary_controls_advanced_tools_executor_and_mini_table_unchanged(sidebar):
    g, widgets, _ = sidebar
    for widget in (g.source_combo, g.monitor_combo, g.mode_combo, g.profile_combo,
                   g.start_btn, g.regions_btn, g.calibrate_btn, g.advanced_tools_btn):
        assert widget.winfo_ismapped()
    assert g.mode_combo.options["values"] == ["Individual", "Multijugador"]
    assert g.source_combo.options["values"] == ["Monitor", "Window"]
    assert g.source_combo.options["state"] == "readonly"
    assert g.start_btn.options["state"] == "disabled"
    assert not g.advanced_tools_open.get() and not g.advanced_tools_frame.winfo_ismapped()
    for text, callback in (("New Round", g._new_round), ("New Shoe", g._new_shoe),
                           ("Ajustes Mini Mesa", g._round_overlay_options)):
        matches = [w for w in widgets if text in w.options.get("text", "")]
        assert len(matches) == 1 and matches[0].options["command"] is callback
    assert g.round_enabled.get() is True
    assert not any(w.options.get("text") == "Cartel de ronda" for w in widgets)
    assert not any(w.options.get("variable") is g.round_enabled for w in widgets)
    executor_titles = [w for w in widgets if w.options.get("text") == "Executor (ghost / assist)"]
    assert len(executor_titles) == 1 and not executor_titles[0].winfo_ismapped()
    assert g.executor.mode == "off"
    g.executor.set_mode.assert_not_called()
    assert g.arm_btn.options["state"] == "disabled"
    GUI._toggle_advanced_tools(g)
    assert g.advanced_tools_open.get() and g.advanced_tools_frame.winfo_ismapped()
    for widget, callback in ((g.ocr_btn, g._calibrate_ocr), (g.controls_btn, g._calibrate_controls),
                             (g.anchors_btn, g._calibrate_anchors)):
        assert widget.options["command"] is callback
    GUI._toggle_advanced_tools(g)
    assert not g.advanced_tools_open.get() and not g.advanced_tools_frame.winfo_ismapped()
    assert all(not section.winfo_ismapped() for section in g._hidden_sidebar_sections.values())


def test_navigation_is_the_only_mini_table_toggle(sidebar):
    g, widgets, _ = sidebar
    for name in ("_open_logs", "_open_stats", "_open_leaks", "_open_bankroll",
                 "_open_trainer", "_open_settings"):
        setattr(g, name, Mock())
    g._toggle_hud = MethodType(GUI._toggle_hud, g)
    g._nav_button = MethodType(GUI._nav_button, g)
    GUI._build_nav_bar(g)
    toggles = [w for w in widgets if w.options.get("command") == g._toggle_hud]
    assert len(toggles) == 1
    toggle = toggles[0]
    assert "Mini Mesa" in toggle.options["text"]
    assert toggle.master is g.nav_bar and toggle.winfo_ismapped()
    assert g.hud_nav_btn is toggle
    assert not any(w.options.get("command") is g._toggle_round_overlay for w in widgets)
    assert not any(w.options.get("variable") is g.round_enabled for w in widgets)
    assert g.round_enabled.get() is True
    toggle.options["command"]()
    assert g.round_enabled.get() is False
    g._toggle_round_overlay.assert_called_once_with()
    toggle.options["command"]()
    assert g.round_enabled.get() is True
    assert g._toggle_round_overlay.call_count == 2
    assert not g.controller.running and not g.controller.configuration_busy


@pytest.mark.parametrize("enabled", [False, True])
def test_mini_table_options_keep_opacity_and_position_without_activation(sidebar, monkeypatch, enabled):
    g, widgets, _ = sidebar
    monkeypatch.setattr(gui_module.constants, "UI", deepcopy(gui_module.constants.UI))
    g.round_enabled.set(enabled)
    overlay = SimpleNamespace(editing=False, set_opacity=Mock(), set_editing=Mock(),
                              set_locked=Mock(), remember_position=Mock())
    g.round_overlay = overlay if enabled else None
    g._round_options = None
    top = Mock()
    top.children = []
    constructor = Mock(return_value=top)
    monkeypatch.setattr(gui_module.tk, "Toplevel", constructor)
    monkeypatch.setattr(gui_module.tk, "DoubleVar", Variable)
    scale = Mock()
    monkeypatch.setattr(gui_module.tk, "Scale", scale)
    GUI._round_overlay_options(g)
    constructor.assert_called_once_with(g)
    assert not any(w.options.get("text") in ("Cartel de ronda", "Mostrar cartel") for w in widgets)
    assert not any(w.options.get("variable") is g.round_enabled for w in widgets)
    scale.call_args.kwargs["command"](65)
    assert gui_module.constants.UI["round_opacity"] == .65
    assert overlay.set_opacity.call_count == int(enabled)
    if enabled:
        overlay.set_opacity.assert_called_once_with(.65)
    position = next(w for w in widgets if "Ajustar posici" in w.options.get("text", ""))
    position.options["variable"].set(True)
    position.options["command"]()
    if enabled:
        overlay.set_editing.assert_called_once_with(True)
        overlay.set_locked.assert_called_once_with(False)
    else:
        assert not position.options["variable"].get()
        assert "botón superior" in g.set_status.call_args.args[0]
        assert g.round_overlay is None
    assert g.round_enabled.get() is enabled
    g._toggle_round_overlay.assert_not_called()
    finish = next(w for w in widgets if w.options.get("text") == "Bloquear y guardar")
    finish.options["command"]()
    if enabled:
        overlay.set_editing.assert_called_with(False)
        overlay.set_locked.assert_called_with(True)
        overlay.remember_position.assert_called_once_with()
        assert g.round_overlay is overlay
    assert g.round_enabled.get() is enabled
    g.controller.engine.persist_settings_async.assert_called_once_with()
    top.destroy.assert_called_once_with()


def test_sidebar_scroll_and_width_handlers_are_preserved(sidebar):
    g, widgets, _ = sidebar
    canvas = g._sidebar_canvas
    scrollbar = next(w for w in widgets if w.options.get("orient") == "vertical")
    inner = next(w for w in canvas.children if "<Configure>" in w.bindings)
    inner.bindings["<Configure>"](SimpleNamespace())
    assert canvas.options["scrollregion"] == (0, 0, 320, 600)
    assert scrollbar.winfo_ismapped()
    canvas.bindings["<Configure>"](SimpleNamespace(width=280))
    assert canvas.item_options[1] == {"width": 280}
    canvas.bbox_value = (0, 0, 280, 200)
    inner.bindings["<Configure>"](SimpleNamespace())
    assert not scrollbar.winfo_ismapped()
    canvas.yview_moveto.assert_called_once_with(0.0)
    g.bind_all.assert_called_once()
