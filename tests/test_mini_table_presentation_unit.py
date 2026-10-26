"""Shared presentation contracts with fake canvases, windows and controllers."""
from copy import deepcopy
from types import MethodType
from unittest.mock import Mock

import pytest

from lib.interfaces import modern_gui as gui_module, round_overlay as overlay_module
from lib.interfaces.modern_gui import ModernBlackjackGUI as GUI
from lib.interfaces.round_overlay import MiniTableOverlay as Overlay, WIDTH, overlay_visibility
from lib.interfaces.capture_guard import CaptureGuard
from lib.logic.table_presentation import PRESENTATION_STATES, build_table_presentation
from tests.test_individual_table_view_unit import view, gui, presentation, card, Variable
from tests.test_overlay_visibility_unit import fake_overlay


class Canvas:
    def __init__(self):
        self.items = {}
        self.bindings = {}
        self.serial = 0
        self.draws = 0

    def delete(self, tag):
        assert tag == "all"
        self.items.clear()
        self.bindings.clear()
        self.draws += 1

    def _create(self, kind, coords, options):
        self.serial += 1
        self.items[self.serial] = {"kind": kind, "coords": coords, **options}
        return self.serial

    def create_rectangle(self, *coords, **options):
        return self._create("rectangle", coords, options)

    def create_text(self, *coords, **options):
        return self._create("text", coords, options)

    def tag_bind(self, tag, event, callback):
        self.bindings[(tag, event)] = callback

    def texts(self):
        return [item.get("text") for item in self.items.values() if item["kind"] == "text"]


@pytest.fixture
def mini(monkeypatch):
    monkeypatch.setattr(overlay_module.constants, "UI", deepcopy(overlay_module.constants.UI))
    o = fake_overlay()
    o.canvas = Canvas()
    o.on_correct_card = Mock(side_effect=AssertionError("No correction is authorized"))
    for name in ("_draw_card", "_draw_group", "_text", "_rect"):
        setattr(o, name, MethodType(getattr(Overlay, name), o))
    return o


def identities(o):
    return [item["card"]["card_id"] for item in o._drawn_cards if "card_id" in item["card"]]


def poll(g):
    GUI._poll_snapshot(g)
    g.log_manager.exception.assert_not_called()


def test_only_presentation_supplies_cards_despite_historical_fields(mini):
    p = presentation()
    p.update(seats=[{"cards": ["Queen of Hearts"], "mine": True, "optimal": "Hit"}],
             card_groups=[{"cards": [card("foreign", "2")]}],
             dealer_cards=[card("fake-dealer", "3")], dealer_legacy={"card": "King"})
    before = deepcopy(p)
    mini.update_from_presentation(p)
    assert identities(mini) == ["d1", "h1", "h2"]
    assert "Hit" not in mini.canvas.texts()
    assert p == before
    assert not hasattr(mini, "update_from_snapshot")


@pytest.mark.parametrize("count", [0, 1, 2, 3, 9, 20])
def test_individual_is_one_centered_hero_with_exact_cards(mini, count):
    cards = [card(f"h{i}", "8") for i in range(count)]
    mini.update_from_presentation(presentation(cards=cards, dealer=[]))
    texts = mini.canvas.texts()
    assert texts.count("Yo") == 1 and texts.count("Crupier") == 1
    assert identities(mini) == [f"h{i}" for i in range(count)]
    assert not any(str(text).startswith(("J1", "Player", "Seat")) for text in texts)
    for title in ("Yo", "Crupier"):
        item = next(item for item in mini.canvas.items.values() if item.get("text") == title)
        assert item["coords"][0] == WIDTH / 2
    if count == 0:
        assert texts.count("[sin cartas]") == 2
    else:
        left = mini.canvas.items[mini._drawn_cards[0]["item"]]["coords"]
        right = mini.canvas.items[mini._drawn_cards[-1]["item"]]["coords"]
        assert (left[0] + right[2]) / 2 == pytest.approx(WIDTH / 2)


@pytest.mark.parametrize("rank,suit,state,reason,label", [
    ("Ace", "S", "confirmed", None, "A♠"),
    ("King", "Hearts", "confirmed", None, "K♥"),
    ("10", "D", "confirmed", None, "10♦"),
    ("Queen", "C", "confirmed", None, "Q♣"),
    ("8", None, "confirmed", None, "8"),
    (None, None, "pending", None, "?"),
    ("Ace", "S", "pending", None, "?"),
    ("Ace", "S", "doubtful", "conflict", "?"),
    ("Ace", "S", "confirmed", "conflict", "?"),
])
def test_rank_suit_and_uncertain_cards_are_rendered_without_invention(mini, rank, suit, state, reason, label):
    c = card(rank=rank, suit=suit, state=state, reason=reason)
    mini.update_from_presentation(presentation(cards=[c], dealer=[]))
    assert label in mini.canvas.texts()
    assert mini._drawn_cards[0]["card"] == c
    assert len(mini._drawn_cards) == 1


def test_multiple_dealer_cards_keep_ids_order_and_no_hidden_card(mini):
    cards = [card("d1", "8"), card("d2", "8"), card("d3", "Ace")]
    mini.update_from_presentation(presentation(cards=[], dealer=cards))
    assert identities(mini) == ["d1", "d2", "d3"]
    assert len(mini._drawn_cards) == 3


def test_legacy_identity_and_physical_metadata_are_detached(mini):
    c = {"rank": "Ace", "state": "confirmed", "suit": None, "hand": 1,
         "legacy_ref": {"seat_index": 4, "card_index": 2}}
    p = presentation(cards=[c], dealer=[])
    mini.update_from_presentation(p)
    visual = mini._drawn_cards[0]
    assert visual["identity"] == {"legacy_ref": c["legacy_ref"], "rank": "Ace",
                                  "suit": None, "state": "confirmed", "hand": 1}
    assert "card_id" not in visual["identity"]
    assert visual["card"]["hand"] == 1
    visual["identity"]["legacy_ref"]["seat_index"] = -1
    assert p["players"][0]["cards"][0]["legacy_ref"]["seat_index"] == 4


def test_split_preserves_one_hero_ids_and_publication_order(mini):
    cards = [card("a", hand=1), card("b", hand=0), card("c", hand=1), card("d", hand=0)]
    mini.update_from_presentation(presentation(cards=cards, dealer=[], split=True))
    assert mini.canvas.texts().count("Yo") == 1
    assert "Mano 1" in mini.canvas.texts() and "Mano 2" in mini.canvas.texts()
    assert identities(mini) == ["a", "b", "c", "d"]
    assert [item["card"] for item in mini._drawn_cards] == cards
    right, left = [mini.canvas.items[item["item"]]["coords"][0] for item in mini._drawn_cards[:2]]
    assert right > left


@pytest.mark.parametrize("tags", [[None, 1], [0, 0], [0, 2], [False, 1]])
def test_incomplete_split_does_not_invent_hands(mini, tags):
    cards = [card(str(i), hand=tag) for i, tag in enumerate(tags)]
    mini.update_from_presentation(presentation(cards=cards, dealer=[], split=True, status="uncertain"))
    assert not any(text in ("Mano 1", "Mano 2") for text in mini.canvas.texts())
    assert any("Split incierto" in str(text) for text in mini.canvas.texts())
    assert [item["card"] for item in mini._drawn_cards] == cards


@pytest.mark.parametrize("status", PRESENTATION_STATES)
def test_all_states_keep_window_visible_and_obey_shared_notice(mini, status):
    p = presentation(status=status)
    mini.update_from_presentation(p, running=status != "stopped")
    assert mini._active and mini.enabled
    mini.win.deiconify.assert_called()
    notice = overlay_visibility(p, status != "stopped")[1]
    assert notice in mini.canvas.texts()
    blocked = status in ("stopped", "error", "needs_calibration", "source_mismatch")
    assert len(mini._drawn_cards) == (0 if blocked else 3)
    assert "SIN CONSEJO" in mini.canvas.texts()


@pytest.mark.parametrize("status", ["stopped", "uncertain", "reading", "waiting", "error", "needs_calibration", "source_mismatch"])
def test_nonready_reading_never_displays_even_explicit_old_advice(mini, status):
    p = presentation(status=status)
    p["advice_available"] = True
    p["players"][0]["advice"] = "Stand"
    mini.update_from_presentation(p, running=status != "stopped")
    assert "Stand" not in mini.canvas.texts()
    assert "SIN CONSEJO" in mini.canvas.texts()


@pytest.mark.parametrize("case", ["disabled", "other", "ambiguous", "missing", "valid"])
def test_recommendations_require_explicit_link_to_one_hero(mini, case):
    p = presentation()
    p["advice_available"] = case != "disabled"
    p["players"][0]["advice"] = None if case == "missing" else "Hit"
    if case == "other":
        p["players"][0]["is_hero"] = False
    if case == "ambiguous":
        p["players"][0]["is_ambiguous"] = True
    mini.update_from_presentation(p)
    assert ("Hit" in mini.canvas.texts()) == (case == "valid")


def test_display_has_no_card_corrections_or_seven_seat_selector(mini):
    mini.update_from_presentation(presentation())
    assert set(mini.canvas.bindings) == {("close", "<Button-1>")}
    assert not any(str(text).startswith(("J1", "J2", "Cambiar jugador", "Corregir carta")) for text in mini.canvas.texts())
    mini.on_correct_card.assert_not_called()


@pytest.mark.parametrize("change", ["round_id", "shoe_id", "cards"])
def test_stale_selections_and_identity_lookup_follow_new_context(mini, change):
    p = presentation()
    mini.update_from_presentation(p)
    ref = {"card_id": "h1", "round_id": 2, "shoe_id": "shoe-a"}
    assert mini._selection_exists(ref)
    mini._selected_id, mini._selected_ref, mini._selected_round = "h1", ref, 2
    if change == "cards":
        p["players"][0]["cards"] = []
    else:
        p[change] = 3 if change == "round_id" else "shoe-b"
    mini.update_from_presentation(p)
    assert mini._selected_id is None and mini._selected_ref is None
    assert not mini._selection_exists(ref)


def test_metrics_accept_only_aggregate_scalars_and_never_drive_reading_state(mini):
    metrics = {"running": 4, "true": 1.5, "count_uncertain": True,
               "seats": [{"advice": "Double"}], "card_groups": [card("bad")],
               "dealer_cards": [card("fake")], "advice": "Double"}
    before = deepcopy(metrics)
    mini.update_from_presentation(presentation(), metrics=metrics)
    assert mini._metrics == {"running": 4, "true": 1.5, "count_uncertain": True}
    assert mini._last_state["status"] == "ready"
    assert identities(mini) == ["d1", "h1", "h2"]
    assert "Double" not in mini.canvas.texts() and metrics == before
    mini.update_from_presentation(presentation(), metrics={"running": {"seats": []}, "true": [], "count_uncertain": "unknown"})
    assert mini._metrics == {}


def test_equivalent_updates_do_not_redraw_but_metrics_changes_do(mini):
    p = presentation()
    mini.update_from_presentation(p, metrics={"running": 2})
    draws = mini.canvas.draws
    mini.update_from_presentation(deepcopy(p), metrics={"running": 2})
    assert mini.canvas.draws == draws
    mini.update_from_presentation(p, metrics={"running": 3})
    assert mini.canvas.draws == draws + 1


def test_capture_guard_hides_during_redraw_and_restores_same_window(mini):
    mini.update_from_presentation(presentation())
    win = mini.win
    guard = CaptureGuard(Mock(), lambda: mini)
    with guard.capture():
        win.deiconify.reset_mock()
        mini.update_from_presentation(presentation(cards=[card("new", "8")]))
        mini._keep_topmost()
        mini._show_noactivate()
        assert mini._capture_hidden and mini.enabled
        win.deiconify.assert_not_called()
    assert not mini._capture_hidden and mini.win is win
    win.deiconify.assert_called_once()
    guard.close()


def test_opacity_and_position_editing_do_not_replace_reading_or_enable_corrections(mini):
    p = presentation()
    mini.update_from_presentation(p)
    mini.set_opacity(.6)
    mini.set_editing(True)
    assert mini.enabled and mini._active and mini._last_state == p
    assert identities(mini) == ["d1", "h1", "h2"]
    assert "AJUSTE DE POSICIÓN" in mini.canvas.texts()
    assert set(mini.canvas.bindings) == {("close", "<Button-1>")}
    mini.set_editing(False)
    assert mini._last_state == p and mini.opacity == .6


def test_multiplayer_uses_no_fixed_seat_fallback(mini):
    p = presentation(mode="multiplayer")
    p["players"] += [{"id": "TMP_1", "seat_id": "TMP_1", "cards": [card("other", "2")]}]
    mini.update_from_presentation(p)
    assert identities(mini) == ["d1"]
    assert any("dinámica pendiente" in str(text) for text in mini.canvas.texts())
    assert "Yo" not in mini.canvas.texts()


def test_gui_builds_once_and_passes_the_same_object_to_both_views(view, mini, monkeypatch):
    g = gui(view)
    g.round_overlay = g.hud = mini
    table_spy = Mock(wraps=view.update)
    mini_spy = Mock(wraps=mini.update_from_presentation)
    view.update, mini.update_from_presentation = table_spy, mini_spy
    builder = Mock(wraps=build_table_presentation)
    monkeypatch.setattr(gui_module, "build_table_presentation", builder)
    # Real _render also calls the shared helper; polling must not duplicate it.
    g._render = MethodType(GUI._render, g)
    for _ in range(3):
        poll(g)
    assert builder.call_count == table_spy.call_count == mini_spy.call_count == 1
    assert table_spy.call_args.args[0] is mini_spy.call_args.args[0] is g._table_presentation
    assert view._last_snapshot == mini._last_state
    assert [item["card"] for item in mini._drawn_cards] == (
        view._last_snapshot["dealer"]["cards"] + view._last_snapshot["players"][0]["cards"])


def test_stop_same_seq_clears_both_views_and_does_not_write_engine(view, mini):
    g = gui(view)
    g.round_overlay = g.hud = mini
    before = deepcopy(g.controller.engine.get_snapshot.return_value)
    poll(g)
    assert len(mini._drawn_cards) == 3 and len(view.seats[0]["cards"]) == 2
    g.controller.running = False
    poll(g)
    assert mini._drawn_cards == [] and view.seats[0]["cards"] == []
    assert view._last_snapshot == mini._last_state
    assert mini._last_state["status"] == "stopped" and mini._active
    assert g.controller.engine.get_snapshot.return_value == before


@pytest.mark.parametrize("field,bad", [("source_profile", "Other"), ("source_mode", "multiplayer"),
                                       ("source_resolution", [320, 180])])
def test_gui_filters_foreign_source_before_building_and_clears_both_views(view, mini, monkeypatch, field, bad):
    g = gui(view)
    g.round_overlay = g.hud = mini
    poll(g)
    g.controller.engine.get_snapshot.return_value[field] = bad
    builder = Mock(wraps=build_table_presentation)
    monkeypatch.setattr(gui_module, "build_table_presentation", builder)
    poll(g)
    assert builder.call_args.args[0] is None
    assert view._last_snapshot == mini._last_state
    assert mini._drawn_cards == [] and view.seats[0]["cards"] == []
    assert mini._metrics == {}


def test_mode_and_calibration_change_are_shared_without_a_new_seq(view, mini):
    g = gui(view)
    g.round_overlay = g.hud = mini
    poll(g)
    g._source_configured = False
    g.mode_var.set("Multijugador")
    poll(g)
    assert view._last_snapshot == mini._last_state
    assert mini._last_state["mode"] == "multiplayer"
    assert mini._last_state["status"] == "needs_calibration"
    assert view.seats == [] and mini._drawn_cards == []


def test_activation_reuses_cache_and_x_close_cannot_be_reopened_by_polling(view, mini, monkeypatch):
    g = gui(view)
    g.round_overlay = g.hud = None
    g.round_enabled = Variable(True)
    g.hud_nav_btn = Mock()
    g._on_hud_closed = MethodType(GUI._on_hud_closed, g)
    mini.on_close = g._on_hud_closed
    builder = Mock(wraps=build_table_presentation)
    factory = Mock(return_value=mini)
    monkeypatch.setattr(gui_module, "build_table_presentation", builder)
    monkeypatch.setattr(overlay_module, "MiniTableOverlay", factory)
    poll(g)
    cached = g._table_presentation
    GUI._toggle_round_overlay(g)
    GUI._toggle_round_overlay(g)
    assert builder.call_count == factory.call_count == 1
    assert factory.call_args.kwargs == {"on_close": g._on_hud_closed}
    assert mini._last_state == cached
    mini._close_by_user()
    assert g.round_overlay is g.hud is None and not g.round_enabled.get()
    poll(g)
    assert factory.call_count == 1 and not mini.win.winfo_exists()


def test_first_activation_without_valid_snapshot_does_not_fetch_stale_cards(view, mini, monkeypatch):
    g = gui(view)
    g.round_overlay = g.hud = None
    g.round_enabled = Variable(True)
    g._on_hud_closed = Mock()
    g.controller.engine.get_snapshot.return_value["source_profile"] = "Other"
    monkeypatch.setattr(overlay_module, "MiniTableOverlay", Mock(return_value=mini))
    GUI._toggle_round_overlay(g)
    assert mini._last_state == view._last_snapshot
    assert mini._drawn_cards == [] and mini._metrics == {}


def test_snapshot_and_hidden_statistics_are_not_mutated_by_shared_render(view, mini):
    g = gui(view)
    g.round_overlay = g.hud = mini
    snap = g.controller.engine.get_snapshot.return_value
    original = deepcopy(snap)
    for variable in list(g.info_vars.values()) + list(g.counter_vars.values()):
        variable.set("unchanged")
    GUI._render_table(g, snap)
    assert snap == original
    assert all(var.get() == "unchanged" for var in list(g.info_vars.values()) + list(g.counter_vars.values()))
    g.controller.engine.get_snapshot.assert_not_called()
