"""Unit contracts for identity-bound card correction UI callbacks.

These tests exercise the GUI handler with a fake engine. They never create a
Tk root, open a dialog, or run capture/inference.
"""
from copy import deepcopy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from lib.interfaces import modern_gui as gui_module
from lib.interfaces.modern_gui import ModernBlackjackGUI as GUI
from lib.interfaces.card_picker import CardPicker
from lib.interfaces.round_overlay import MiniTableOverlay
from lib.logic.table_presentation import build_table_presentation
from tests.test_individual_table_view_unit import (
    gui, presentation, table_module, TableView, Widget,
)


def make_gui(engine=None):
    g = gui()
    g.controller.engine.apply_correction = Mock(
        return_value=SimpleNamespace(ok=True, reason="applied"))
    g.controller.engine.counter = SimpleNamespace(shoe_id="shoe-a")
    records = {"h1": SimpleNamespace(id="h1", round_id=2,
                                     status="active", state="confirmed")}
    g.controller.engine.card_registry = SimpleNamespace(cards=records)
    if engine is not None:
        g.controller.engine = engine
    g._table_presentation = presentation()
    g._table_presentation.update(
        profile_name="Profile", mode="individual", resolution=[1000, 700],
        source_id=None,
        source_ref={"profile_name": "Profile", "mode": "individual",
                    "resolution": [1000, 700], "source_context": None})
    g.set_status = Mock()
    g.ui_actions = SimpleNamespace(record=Mock())
    g._apply_identity_correction = MethodType(GUI._apply_identity_correction, g)
    return g


def identity(**changes):
    value = {"card_id": "h1", "round_id": 2, "shoe_id": "shoe-a",
             "profile_name": "Profile", "mode": "individual",
             "resolution": [1000, 700], "source_id": None,
             "source_ref": {"profile_name": "Profile", "mode": "individual",
                             "resolution": [1000, 700], "source_context": None}}
    value.update(changes)
    return value


def test_correction_is_bound_to_card_identity_and_expected_round():
    g = make_gui()
    ref = identity()
    assert g._apply_identity_correction(ref, "set_rank", new_rank="6") is True
    g.controller.engine.apply_correction.assert_called_once_with(
        "h1", 2, "set_rank", new_rank="6", new_suit=None)


def test_action_payload_passes_rank_and_suit_without_reinterpreting_them():
    g = make_gui()
    assert g._apply_identity_correction(identity(), "set_rank", new_rank="Ace", new_suit="S")
    g.controller.engine.apply_correction.assert_called_once_with(
        "h1", 2, "set_rank", new_rank="Ace", new_suit="S")


def test_ui_never_translates_card_identity_to_seat_or_card_indexes():
    g = make_gui()
    ref = identity(seat_id="J4", seat_index=3, card_index=1)
    g._apply_identity_correction(ref, "set_rank", new_rank="9")
    args, kwargs = g.controller.engine.apply_correction.call_args
    assert args == ("h1", 2, "set_rank")
    assert kwargs == {"new_rank": "9", "new_suit": None}


@pytest.mark.parametrize("ref", [identity(card_id=None), identity(card_id=""),
                                  identity(round_id=None), identity(round_id="bad")])
def test_missing_or_malformed_identity_is_rejected_before_engine_call(ref):
    g = make_gui()
    assert g._apply_identity_correction(ref, "set_rank", new_rank="8") is False
    g.controller.engine.apply_correction.assert_not_called()


def test_dealer_card_uses_the_same_physical_identity_route():
    g = make_gui()
    g._table_presentation["dealer"]["cards"] = [
        {"card_id": "dealer-physical-1", "rank": "7", "suit": "H", "state": "confirmed"}]
    g.controller.engine.card_registry.cards["dealer-physical-1"] = SimpleNamespace(
        id="dealer-physical-1", round_id=2, status="active", state="confirmed")
    ref = identity(card_id="dealer-physical-1")
    assert g._apply_identity_correction(ref, "set_rank", new_rank="8")
    g.controller.engine.apply_correction.assert_called_once_with(
        "dealer-physical-1", 2, "set_rank", new_rank="8", new_suit=None)


@pytest.mark.parametrize("card", [
    {"rank": "Ace", "suit": "S", "state": "confirmed", "legacy_ref": {"seat_index": 2}},
    {"card_id": None, "rank": "Ace", "state": "confirmed"},
    {"card_id": "", "rank": "Ace", "state": "confirmed"},
])
def test_legacy_unknown_or_unidentified_cards_are_never_given_synthetic_ids(card):
    g = make_gui()
    g._table_presentation["players"][0]["cards"] = [card]
    assert g._apply_identity_correction({"legacy_ref": card.get("legacy_ref"),
                                          "round_id": 2, "shoe_id": "shoe-a"},
                                         "set_rank", new_rank="2") is False
    g.controller.engine.apply_correction.assert_not_called()


@pytest.mark.parametrize("state,rank,reason", [
    ("pending", None, None), ("doubtful", "Ace", "conflict"),
])
def test_active_pending_or_uncertain_card_can_be_confirmed_by_physical_identity(state, rank, reason):
    g = make_gui()
    c = {"card_id": "physical-card-17", "rank": rank, "suit": "S", "state": state,
         "reason": reason}
    g._table_presentation["players"][0]["cards"] = [c]
    g.controller.engine.card_registry.cards["physical-card-17"] = SimpleNamespace(
        id="physical-card-17", round_id=2, status="active", state=state)
    assert g._apply_identity_correction(
        identity(card_id="physical-card-17"), "set_rank", new_rank="2") is True
    g.controller.engine.apply_correction.assert_called_once_with(
        "physical-card-17", 2, "set_rank", new_rank="2", new_suit=None)


def test_not_a_card_action_keeps_identity_and_has_no_rank_payload():
    g = make_gui()
    assert g._apply_identity_correction(identity(), "not_a_card") is True
    g.controller.engine.apply_correction.assert_called_once_with(
        "h1", 2, "not_a_card", new_rank=None, new_suit=None)


@pytest.mark.parametrize("rank,suit", [("10", None), ("Queen", "D"), ("Ace", "S")])
def test_rank_and_optional_suit_choices_are_forwarded_as_selected(rank, suit):
    g = make_gui()
    assert g._apply_identity_correction(identity(), "set_rank", new_rank=rank, new_suit=suit)
    g.controller.engine.apply_correction.assert_called_once_with(
        "h1", 2, "set_rank", new_rank=rank, new_suit=suit)


def test_cancel_or_empty_rank_choice_is_a_noop():
    g = make_gui()
    assert g._apply_identity_correction(identity(), "set_rank", new_rank=None) is False
    g.controller.engine.apply_correction.assert_not_called()


def _picker_state(callback, *, current_rank="Ace", current_suit="S",
                  chosen_rank="6", chosen_suit=None):
    picker = SimpleNamespace(
        _chosen_rank=chosen_rank, _current_rank=current_rank,
        _chosen_suit=chosen_suit, _current_suit=current_suit,
        _card_identity=identity(), _on_select=callback, _closed=False,
        _on_close=None, destroy=Mock())
    picker._close = MethodType(CardPicker._close, picker)
    return picker


def test_picker_can_change_only_rank_while_preserving_current_suit():
    callback = Mock()
    picker = _picker_state(callback, chosen_rank="6", chosen_suit=None)
    CardPicker._save_identity_correction(picker)
    callback.assert_called_once_with("set_rank", "6", None, identity())


def test_picker_can_change_only_suit_while_preserving_current_rank():
    callback = Mock()
    picker = _picker_state(callback, chosen_rank="Ace", chosen_suit="H")
    CardPicker._save_identity_correction(picker)
    callback.assert_called_once_with("set_rank", "Ace", "H", identity())


def test_picker_can_change_rank_and_suit_together():
    callback = Mock()
    picker = _picker_state(callback, chosen_rank="9", chosen_suit="D")
    CardPicker._save_identity_correction(picker)
    callback.assert_called_once_with("set_rank", "9", "D", identity())


def test_picker_discard_and_cancel_are_distinct_and_cancel_has_no_effect():
    callback = Mock()
    discard = _picker_state(callback)
    CardPicker._discard_identity_card(discard)
    callback.assert_called_once_with("not_a_card", None, None, identity())

    callback.reset_mock()
    cancel = _picker_state(callback)
    CardPicker._cancel(cancel)
    callback.assert_not_called()


def test_engine_rejection_is_reported_to_the_user():
    g = make_gui()
    g.controller.engine.apply_correction.return_value = SimpleNamespace(
        ok=False, reason="round_changed")
    assert g._apply_identity_correction(identity(), "set_rank", new_rank="5") is False
    g.set_status.assert_called()
    assert g.set_status.call_args.kwargs.get("error") is True


@pytest.mark.parametrize("field,value", [("round_id", 3), ("shoe_id", "shoe-b")])
def test_old_callback_is_rejected_after_round_or_shoe_context_changes(field, value):
    g = make_gui()
    old_ref = identity()
    g._table_presentation[field] = value
    assert g._apply_identity_correction(old_ref, "set_rank", new_rank="5") is False
    g.controller.engine.apply_correction.assert_not_called()


@pytest.mark.parametrize("change", [
    "profile", "mode", "resolution", "source",
])
def test_configuration_change_while_picker_open_rejects_stale_identity(change):
    g = make_gui()
    if change == "profile":
        g.profile_var.set("Other")
    elif change == "mode":
        g.mode_var.set("Multijugador")
    elif change == "resolution":
        g.monitor.width = 800
    else:
        g.controller.engine.get_snapshot.return_value["source_context"] = {
            "kind": "window", "source_id": "different-source",
            "width": 1000, "height": 700}
    assert g._apply_identity_correction(identity(), "set_rank", new_rank="5") is False
    g.controller.engine.apply_correction.assert_not_called()


def test_missing_registry_card_is_rejected_even_when_old_presentation_still_has_it():
    g = make_gui()
    g.controller.engine.card_registry.cards.clear()
    assert g._apply_identity_correction(identity(), "set_rank", new_rank="5") is False
    g.controller.engine.apply_correction.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"new_rank": "Joker"}, {"new_rank": "8", "new_suit": "X"},
])
def test_invalid_rank_or_suit_is_rejected_before_engine_call(kwargs):
    g = make_gui()
    assert g._apply_identity_correction(identity(), "set_rank", **kwargs) is False
    g.controller.engine.apply_correction.assert_not_called()


def test_two_same_rank_cards_keep_independent_physical_ids():
    g = make_gui()
    g._table_presentation["players"][0]["cards"] = [
        {"card_id": "h1", "rank": "8", "suit": "S", "state": "confirmed"},
        {"card_id": "h2", "rank": "8", "suit": "H", "state": "confirmed"},
    ]
    g.controller.engine.card_registry.cards["h1"].rank = "8"
    g.controller.engine.card_registry.cards["h2"] = SimpleNamespace(
        id="h2", round_id=2, status="active", state="confirmed")
    assert g._apply_identity_correction(identity(card_id="h1"),
                                        "set_rank", new_rank="9")
    assert g._apply_identity_correction(identity(card_id="h2"),
                                        "set_rank", new_rank="7")
    assert [call.args[0] for call in g.controller.engine.apply_correction.call_args_list] == ["h1", "h2"]


def test_second_click_raises_existing_picker_instead_of_opening_another(monkeypatch):
    g = make_gui()
    old_picker = SimpleNamespace(winfo_exists=Mock(return_value=True), lift=Mock())
    g._card_correction_picker = old_picker
    picker = Mock()
    monkeypatch.setattr(gui_module, "CardPicker", picker)
    assert GUI._on_identity_card_click(g, identity()) is False
    old_picker.lift.assert_called_once()
    picker.assert_not_called()


def test_widget_identity_is_detached_and_preserves_card_id_round_and_shoe(monkeypatch):
    for name in ("Canvas", "Label", "Frame", "Button"):
        monkeypatch.setattr(table_module.tk, name, Widget)
    monkeypatch.setattr(table_module.scaling, "on_change", Mock())
    monkeypatch.setattr(table_module.scaling, "size", lambda size: tuple(size))
    monkeypatch.setattr(table_module.scaling, "px", lambda value: value)
    monkeypatch.setattr(table_module.Image, "open", Mock(side_effect=lambda _: Image.new("RGB", (300, 436), "white")))
    monkeypatch.setattr(table_module.ImageTk, "PhotoImage", Mock(side_effect=lambda image: object()))
    view = TableView(None, on_card_identity_click=Mock())
    p = presentation()
    target = p["players"][0]["cards"][0]
    before = deepcopy(target)
    # Exercise the table renderer's public update path and inspect the actual
    # widget metadata used by the popup callback.
    view.update(p)
    widget = view.seats[0]["cards"][0]
    assert (widget.card_identity["card_id"], widget.card_identity["round_id"],
            widget.card_identity["shoe_id"], widget.card_identity["mode"]) == (
                target["card_id"], 2, "shoe-a", "individual")
    assert widget.card_identity["rank"] == target["rank"]
    widget.card_identity["card_id"] = "mutated"
    assert target == before


def test_table_click_dispatches_a_detached_identity_without_visual_indexes():
    callback = Mock()
    table = SimpleNamespace(on_card_identity_click=callback)
    widget = SimpleNamespace(card_identity=identity(hand=1, owner="mine"))
    TableView._dispatch_card_identity(table, widget)
    dispatched = callback.call_args.args[0]
    assert dispatched == widget.card_identity
    assert dispatched is not widget.card_identity
    assert "slot" not in dispatched and "seat_index" not in dispatched


def test_card_picker_save_returns_the_identity_captured_when_opened():
    received = Mock()
    captured = identity()
    picker = _picker_state(received, chosen_rank="6", chosen_suit="H")
    picker._card_identity = captured
    CardPicker._save_identity_correction(picker)
    assert received.call_args.args == ("set_rank", "6", "H", captured)
    assert received.call_args.args[3] is not captured
    assert picker.destroy.called


def test_mini_table_click_dispatches_only_a_live_physical_identity():
    class Canvas:
        def __init__(self):
            self.bindings = {}

        def create_rectangle(self, *args, **kwargs):
            return 1

        def create_text(self, *args, **kwargs):
            return 2

        def tag_bind(self, tag, event, callback):
            self.bindings[(tag, event)] = callback

    p = presentation()
    ref = p["players"][0]["cards"][0]
    overlay = MiniTableOverlay.__new__(MiniTableOverlay)
    overlay.canvas = Canvas()
    overlay._last_state = p
    overlay._drawn_cards = []
    overlay.identity_corrections_enabled = True
    overlay.on_correct_card = Mock()
    MiniTableOverlay._draw_card(overlay, 10, 10, ref)
    assert ("physical-card-0", "<ButtonPress-1>") in overlay.canvas.bindings
    MiniTableOverlay._dispatch_identity(overlay, overlay._drawn_cards[0]["identity"])
    overlay.on_correct_card.assert_called_once_with(
        {"card_id": "h1", "rank": "Ace", "suit": None,
         "state": "confirmed", "hand": 0})


def test_gui_picker_callback_routes_selected_card_to_registry_by_identity(monkeypatch):
    g = make_gui()
    g.table.card_image = Mock()
    g._identity_is_current = MethodType(GUI._identity_is_current, g)
    g._finish_identity_correction = MethodType(GUI._finish_identity_correction, g)
    g._clear_identity_picker = MethodType(GUI._clear_identity_picker, g)
    picker = Mock()
    monkeypatch.setattr(gui_module, "CardPicker", picker)
    ref = identity()
    assert GUI._on_identity_card_click(g, ref)
    assert picker.call_args.kwargs["card_identity"] == ref
    on_select = picker.call_args.args[3]
    assert on_select("set_rank", "10", "C", ref)
    g.controller.engine.apply_correction.assert_called_once_with(
        "h1", 2, "set_rank", new_rank="10", new_suit="C")


def test_correction_does_not_mutate_historical_advice_or_split_metadata():
    g = make_gui()
    p = g._table_presentation
    p["advice_available"] = True
    p["players"][0].update(advice="Split", has_split=True)
    p["players"][0]["cards"][0]["hand"] = 0
    before = deepcopy(p)
    g._apply_identity_correction(identity(), "set_rank", new_rank="6")
    assert p == before


def test_successful_correction_does_not_mutate_views_or_presentation_locally():
    g = make_gui()
    before = deepcopy(g._table_presentation)
    assert g._apply_identity_correction(identity(), "set_rank", new_rank="6")
    g.table.update.assert_not_called()
    g.table.remember_snapshot.assert_not_called()
    assert g._table_presentation == before


def test_table_presentation_stamps_physical_identity_with_current_source_context():
    snapshot = {
        "source_mode": "individual", "source_profile": "Profile A",
        "source_resolution": [1920, 1080], "source_context": {
            "kind": "window", "source_id": "browser|game", "width": 1920,
            "height": 1080},
        "shoe_id": "shoe-55", "round": 7,
        "card_groups": [{"seat_id": "J4", "is_hero": True,
                         "cards": [{"card_id": "physical-1", "rank": "8",
                                    "suit": "H", "state": "confirmed",
                                    "owner": "mine", "hand": 1}]}],
        "dealer_cards": [{"card_id": "dealer-1", "rank": "K", "suit": "S",
                          "state": "confirmed", "owner": "dealer"}],
        "reading": {}, "count": {},
    }
    result = build_table_presentation(snapshot, running=True)
    player_card = result["players"][0]["cards"][0]
    dealer_card = result["dealer"]["cards"][0]
    for card, group in ((player_card, "hero"), (dealer_card, "dealer")):
        assert card["card_id"] in ("physical-1", "dealer-1")
        assert (card["round_id"], card["shoe_id"]) == (7, "shoe-55")
        assert (card["profile_name"], card["mode"], card["resolution"]) == (
            "Profile A", "individual", [1920, 1080])
        assert card["source_id"] == "browser|game"
        assert card["source_ref"] == result["source_ref"]
        assert card["group_id"] == group
    assert player_card["hand"] == 1


@pytest.mark.parametrize("delta,should_select", [(4.9, True), (5, False), (18, False)])
def test_mini_table_drag_threshold_never_selects_cards(delta, should_select):
    class Canvas:
        def find_withtag(self, tag):
            return (1,)

        def gettags(self, item):
            return ("physical-card-0",)

    class Window:
        def winfo_x(self):
            return 100

        def winfo_y(self):
            return 200

        def geometry(self, geometry):
            self.last_geometry = geometry

    p = presentation()
    card_data = p["players"][0]["cards"][0]
    overlay = MiniTableOverlay.__new__(MiniTableOverlay)
    overlay.canvas = Canvas()
    overlay.win = Window()
    overlay.editing = True
    overlay._pointer_start = None
    overlay._pressed_identity = None
    overlay._drag_origin = None
    overlay.identity_corrections_enabled = True
    overlay.on_correct_card = Mock()
    overlay._last_state = p
    overlay._drawn_cards = [{"tag": "physical-card-0", "identity": {
        "card_id": card_data["card_id"]}}]
    press = SimpleNamespace(x=500, y=100, x_root=500, y_root=300)
    release = SimpleNamespace(x_root=500 + delta, y_root=300)
    MiniTableOverlay._pointer_press(overlay, press)
    MiniTableOverlay._pointer_release(overlay, release)
    assert overlay.on_correct_card.called is should_select


@pytest.mark.parametrize("delta,moved", [(4, False), (5, True), (20, True)])
def test_window_drag_starts_at_five_pixels_and_never_selects_a_card(delta, moved):
    class Canvas:
        def find_withtag(self, tag):
            return ()

        def gettags(self, item):
            return ()

    class Window:
        x, y = 100, 200

        def winfo_x(self):
            return self.x

        def winfo_y(self):
            return self.y

        def geometry(self, geometry):
            self.last_geometry = geometry

    overlay = MiniTableOverlay.__new__(MiniTableOverlay)
    overlay.canvas = Canvas()
    overlay.win = Window()
    overlay.editing = True
    overlay._pointer_start = None
    overlay._pressed_identity = None
    overlay._drag_origin = None
    overlay.identity_corrections_enabled = True
    overlay.on_correct_card = Mock()
    overlay._last_state = presentation()
    overlay._drawn_cards = []
    press = SimpleNamespace(x=100, y=20, x_root=500, y_root=300)
    move = SimpleNamespace(x_root=500 + delta, y_root=300)
    release = SimpleNamespace(x_root=500 + delta, y_root=300)
    MiniTableOverlay._pointer_press(overlay, press)
    MiniTableOverlay._drag_move(overlay, move)
    MiniTableOverlay._pointer_release(overlay, release)
    assert hasattr(overlay.win, "last_geometry") is moved
    overlay.on_correct_card.assert_not_called()
