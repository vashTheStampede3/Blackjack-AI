"""Main-table contracts with fake widgets; no Tk root, capture or inference."""
from copy import deepcopy
import math
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from lib.interfaces import table_view as table_module, modern_gui as gui_module
from lib.interfaces.table_view import TableView, seat_positions
from lib.interfaces.modern_gui import ModernBlackjackGUI as GUI
from lib.logic.table_presentation import PRESENTATION_STATES, build_table_presentation


class Widget:
    def __init__(self, parent=None, **options):
        self.parent = parent
        self.options = dict(options)
        self.bindings = {}
        self.children = []
        self.mapped = False
        self.destroyed = False
        self.position = {}
        self.width, self.height = 1000, 700
        self.jobs = {}
        self.cancelled = []
        self.items = {}
        self.config_calls = []
        if isinstance(parent, Widget):
            parent.children.append(self)

    def config(self, **options):
        self.options.update(options)
        self.config_calls.append(options)

    configure = config

    def cget(self, name):
        return self.options.get(name, "")

    def bind(self, event, callback, **options):
        self.bindings[event] = callback

    def place(self, **position):
        self.mapped = True
        self.position = position

    def place_forget(self):
        self.mapped = False

    def pack(self, **position):
        self.mapped = True

    def pack_forget(self):
        self.mapped = False

    def grid(self, **position):
        self.mapped = True

    def rowconfigure(self, *args, **options):
        pass

    def columnconfigure(self, *args, **options):
        pass

    def winfo_ismapped(self):
        return self.mapped

    def winfo_exists(self):
        return not self.destroyed

    def destroy(self):
        self.destroyed, self.mapped = True, False
        for child in self.children:
            child.destroy()

    def lift(self):
        pass

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def after(self, delay, callback):
        job = len(self.jobs) + 1
        self.jobs[job] = (delay, callback)
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)

    def create_image(self, x, y, **options):
        item = len(self.items) + 1
        self.items[item] = (x, y, options)
        return item

    def coords(self, item, x, y):
        self.items[item] = (x, y, self.items[item][2])

    def delete(self, item):
        del self.items[item]


class Variable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def card(identity="h1", rank="Ace", suit=None, state="confirmed", hand=0, **extra):
    return dict(card_id=identity, rank=rank, suit=suit, state=state, hand=hand, **extra)


def presentation(*, cards=None, dealer=None, status="ready", mode="individual", split=False):
    return {"mode": mode, "status": status, "message": f"mensaje {status}",
            "seq": 8, "round_id": 2, "shoe_id": "shoe-a", "issues": [],
            "dealer": {"cards": [card("d1", "7")] if dealer is None else dealer},
            "players": [{"id": "hero", "seat_id": "J4", "label": "Yo", "is_hero": True,
                         "is_ambiguous": False, "has_split": split, "closed_by_double": False,
                         "cards": [card(), card("h2", "King")] if cards is None else cards,
                         "advice": None}], "advice_available": False}


@pytest.fixture
def view(monkeypatch):
    for name in ("Canvas", "Label", "Frame", "Button"):
        monkeypatch.setattr(table_module.tk, name, Widget)
    monkeypatch.setattr(table_module.scaling, "on_change", Mock())
    monkeypatch.setattr(table_module.scaling, "size", lambda size: tuple(size))
    monkeypatch.setattr(table_module.scaling, "px", lambda value: value)
    monkeypatch.setattr(table_module.Image, "open", Mock(side_effect=lambda _: Image.new("RGB", (300, 436), "white")))
    monkeypatch.setattr(table_module.ImageTk, "PhotoImage", Mock(side_effect=lambda image: object()))
    callbacks = [Mock() for _ in range(6)]
    table = TableView(None, *callbacks)
    table.test_callbacks = callbacks
    return table


@pytest.mark.parametrize("n", [0, 1, 2, 7])
@pytest.mark.parametrize("size", [(1000, 700, 60, 88), (1500, 1050, 90, 132), (80, 80, 60, 88)])
def test_geometry_is_finite_symmetric_and_centered(n, size):
    w, h, cw, ch = size
    points = seat_positions(w, h, n, cw, ch)
    assert len(points) == n
    assert all(math.isfinite(v) for point in points for v in point)
    for left, right in zip(points, reversed(points)):
        assert left[0] + right[0] == pytest.approx(w)
        assert left[1] == pytest.approx(right[1])
    if n == 1:
        assert points[0][0] == w / 2
    if n > 1:
        assert all(a[0] < b[0] for a, b in zip(points, points[1:]))


def test_individual_constructs_only_hero_and_dealer_without_old_seats(view):
    assert len(view.seats) == 1
    assert view.seats[0]["id"] == "hero"
    assert view.seats[0]["name"].cget("text") == "Yo"
    assert view.dealer_title.cget("text") == "Crupier"
    assert not any("Player" in str(widget.cget("text")) for widget in view.canvas.children)
    assert not any(widget.cget("text") in ("+", "Split", "Undo split") for widget in view.canvas.children)


@pytest.mark.parametrize("n", [0, 1, 2, 3, 6])
def test_exact_card_quantity_without_placeholders_or_deduplication(view, n):
    cards = [card(f"h{i}", "8") for i in range(n)]
    view.update(presentation(cards=cards, dealer=[]))
    assert len(view.seats[0]["cards"]) == n
    assert [w.card_data["card_id"] for w in view.seats[0]["cards"]] == [f"h{i}" for i in range(n)]
    assert view.dealer["cards"] == []
    assert view.seats[0]["empty"].mapped == (n == 0)


@pytest.mark.parametrize("rank,suit,state,reason,expected", [
    ("Ace", "S", "confirmed", None, "Ace of Spades"),
    ("King", "Hearts", "confirmed", None, "King of Hearts"),
    ("10", "D", "confirmed", None, "10 of Diamonds"),
    ("Queen", "C", "confirmed", None, "Queen of Clubs"),
    ("Ace", None, "confirmed", None, "Ace"),
    (None, "S", "pending", None, "unknown"),
    ("Ace", None, "pending", None, "unknown"),
    ("Ace", "S", "doubtful", "conflict", "unknown"),
    ("Ace", "S", "confirmed", "conflict", "unknown"),
])
def test_visual_card_uses_actual_names_without_inventing_rank_or_suit(view, rank, suit, state, reason, expected):
    original = card(rank=rank, suit=suit, state=state, reason=reason)
    view.update(presentation(cards=[original]))
    assert view.seats[0]["rendered"] == [expected]
    assert view.seats[0]["cards"][0].card_data == original
    assert view.dealer["rendered"] == ["7"]


def test_empty_table_has_text_placeholders_and_no_physical_card_widgets(view):
    p = presentation(cards=[], dealer=[], status="waiting")
    p["message"] = "Esperando cartas"
    view.update(p)
    assert view.dealer["cards"] == view.seats[0]["cards"] == []
    assert view.dealer["empty"].cget("text") == "[sin cartas]"
    assert view.seats[0]["empty"].mapped and view.dealer["empty"].mapped
    assert view.status_label.cget("text") == "Esperando cartas"


@pytest.mark.parametrize("status", PRESENTATION_STATES)
def test_states_use_published_message_and_withdraw_old_cards_when_blocked(view, status):
    view.update(presentation())
    p = presentation(status=status)
    view.update(p)
    assert view.status_label.cget("text") == p["message"]
    blocked = status in ("stopped", "error", "needs_calibration", "source_mismatch")
    assert len(view.seats[0]["cards"]) == (0 if blocked else 2)
    assert len(view.dealer["cards"]) == (0 if blocked else 1)
    assert view.dealer_title.mapped and view.seats[0]["name"].mapped


def test_unbacked_advice_and_historical_seat_fields_never_appear(view):
    p = presentation()
    p["players"][0].update(advice="old advice", optimal="old optimal", index_advice="old index", total="21")
    p["seats"] = [{"index": 0, "advice": "historic seat"}]
    p["insurance"] = {"text": "old insurance"}
    view.update(p)
    assert view.seats[0]["advice"].cget("text") == ""
    assert not any(w.cget("text") in ("old advice", "old optimal", "old index", "21", "old insurance", "historic seat")
                   for w in view.canvas.children)


def test_advice_is_removed_when_presentation_withdraws_it(view):
    p = presentation()
    p["advice_available"] = True
    p["players"][0]["advice"] = "Verified advice"
    view.update(p)
    assert view.seats[0]["advice"].cget("text") == "Verified advice"
    view.update(presentation())
    assert view.seats[0]["advice"].cget("text") == ""


def test_split_is_one_player_with_two_separate_labeled_hands(view):
    cards = [card("a", hand=0), card("b", hand=1), card("c", hand=0), card("d", hand=1)]
    view.update(presentation(cards=cards, split=True))
    seat = view.seats[0]
    assert len(view.seats) == 1 and seat["split_valid"]
    assert [w.cget("text") for w in seat["hand_titles"]] == ["Mano 1", "Mano 2"]
    assert [w.card_data for w in seat["cards"]] == cards
    assert len({id(w) for w in seat["cards"]}) == 4
    assert seat["cards"][0].position["x"] < seat["cards"][1].position["x"]
    assert all(w.mapped for w in seat["hand_titles"])


@pytest.mark.parametrize("tags", [[0, None], [0, 0], [0, 2], [False, 1]])
def test_incomplete_split_keeps_received_cards_without_inventing_distribution(view, tags):
    cards = [card(f"h{i}", hand=tag) for i, tag in enumerate(tags)]
    view.update(presentation(cards=cards, split=True, status="uncertain"))
    seat = view.seats[0]
    assert not seat["split_valid"]
    assert [w.card_data for w in seat["cards"]] == cards
    assert "incierto" in seat["split_notice"].cget("text")
    assert not any(w.mapped for w in seat["hand_titles"])


def test_no_index_clicks_or_unsafe_controls_are_bound_to_cards_or_advice(view):
    view.update(presentation(split=True, cards=[card("a", hand=0), card("b", hand=1)]))
    for widget in view.canvas.children:
        assert widget.cget("cursor") != "hand2"
        callback = widget.bindings.get("<Button-1>")
        if callback:
            callback(SimpleNamespace())
    for callback in view.test_callbacks:
        callback.assert_not_called()


def test_widget_identity_is_detached_and_contains_no_visual_slot(view):
    c = card(legacy_ref={"seat_index": 4, "card_index": 1}, hand=1)
    p = presentation(cards=[c])
    view.update(p)
    widget = view.seats[0]["cards"][0]
    assert widget.card_identity == {"card_id": "h1", "legacy_ref": c["legacy_ref"], "round_id": 2, "shoe_id": "shoe-a"}
    assert widget.card_data["hand"] == 1
    widget.card_identity["legacy_ref"]["seat_index"] = -1
    assert c["legacy_ref"]["seat_index"] == 4


def test_equivalent_updates_reuse_widgets_photos_and_decoded_images(view):
    p = presentation(cards=[card(suit="S"), card("b", "8")])
    view.update(p)
    widgets = list(view.seats[0]["cards"])
    photos = [widget.image for widget in widgets]
    calls = table_module.ImageTk.PhotoImage.call_count
    decodes = table_module.Image.open.call_count
    view.update(deepcopy(p))
    assert view.seats[0]["cards"] == widgets
    assert [w.image for w in widgets] == photos
    assert table_module.ImageTk.PhotoImage.call_count == calls
    assert table_module.Image.open.call_count == decodes


def test_growing_and_shrinking_hand_only_adds_or_removes_needed_widgets(view):
    view.update(presentation(cards=[card()]))
    first = view.seats[0]["cards"][0]
    child_count = len(view.canvas.children)
    view.update(presentation(cards=[card(), card("b", "8")]))
    assert len(view.canvas.children) == child_count + 1
    second = view.seats[0]["cards"][1]
    assert view.seats[0]["cards"][0] is first
    view.update(presentation(cards=[card()]))
    assert second.destroyed and not first.destroyed


def test_same_image_new_identity_updates_widget_metadata(view):
    view.update(presentation(cards=[card("old", "8")]))
    widget = view.seats[0]["cards"][0]
    image = widget.image
    view.update(presentation(cards=[card("new", "8")]))
    assert widget.image is image and widget.card_identity["card_id"] == "new"


def test_resize_debounce_keeps_presentation_and_centered_titles(view):
    p = presentation()
    view.update(p)
    view._on_resize(None)
    first_job = view._resize_job
    view._on_resize(None)
    assert view.canvas.cancelled == [first_job]
    assert view.canvas.jobs[view._resize_job][0] == 120
    view.canvas.width = 1200
    view.canvas.jobs[view._resize_job][1]()
    assert view._last_snapshot == p
    assert view.seats[0]["name"].position["x"] == 600
    assert view.dealer_title.position["x"] == 600
    assert view.seats[0]["name"].position["y"] > view.dealer_title.position["y"] + 123


def test_dpi_rescale_preserves_identity_and_reuses_decoded_masters(view, monkeypatch):
    p = presentation(cards=[card(suit="S")])
    view.update(p)
    widget = view.seats[0]["cards"][0]
    identity = deepcopy(widget.card_identity)
    old_photo = widget.image
    master = view._master_cache["Ace of Spades"]
    monkeypatch.setattr(table_module.scaling, "size", lambda size: tuple(round(v * 1.5) for v in size))
    monkeypatch.setattr(table_module.scaling, "px", lambda value: round(value * 1.5))
    view._rescale()
    assert widget.card_identity == identity
    assert widget.image is not old_photo
    assert view._master_cache["Ace of Spades"] is master
    assert view._last_snapshot == p


def test_neutral_rank_and_full_card_have_distinct_cached_images(view):
    names = ["unknown", "Ace", "Ace of Spades"]
    images = [view.card_image(name) for name in names]
    assert len({id(image) for image in images}) == 3
    assert all(view.card_image(name) is image for name, image in zip(names, images))
    assert view._master("unknown") is view._master_cache["unknown"]


def test_preview_hides_cards_and_restores_latest_presentation(view):
    p = presentation()
    view.update(p)
    view.show_preview(Image.new("RGB", (500, 300)), Mock())
    assert all(not w.mapped for hand in [view.dealer] + view.seats for w in view._hand_widgets(hand))
    latest = presentation(cards=[card("new", "9")], dealer=[])
    view.update(latest)
    assert view._last_snapshot == latest
    assert not any(w.mapped for w in view.seats[0]["cards"])
    view.clear_preview()
    assert view._preview_item is None
    assert [w.card_identity["card_id"] for w in view.seats[0]["cards"]] == ["new"]
    assert view.seats[0]["cards"][0].mapped


@pytest.mark.parametrize("next_state", ["stopped", "source_mismatch"])
def test_preview_does_not_restore_cards_after_stop_or_source_change(view, next_state):
    view.update(presentation())
    view.show_preview(Image.new("RGB", (400, 200)), Mock())
    view.update(presentation(status=next_state))
    view.clear_preview()
    assert view.seats[0]["cards"] == [] and view.dealer["cards"] == []


def test_preview_resize_dpi_and_mode_change_restore_no_fixed_seats(view):
    view.update(presentation())
    view.show_preview(Image.new("RGB", (500, 300)), Mock())
    latest = presentation(mode="multiplayer")
    view.update(latest)
    view.canvas.width = 1300
    view._relayout()
    assert view.canvas.items[view._preview_item][0] == 650
    view._rescale()
    view.clear_preview()
    assert view.seats == []
    assert "dinámica pendiente" in view.mode_label.cget("text")
    assert len(view.dealer["cards"]) == 1
    view.update(presentation(cards=[], dealer=[]))
    assert len(view.seats) == 1 and view.seats[0]["cards"] == []


def test_view_never_mutates_input_or_remembered_presentation(view):
    p = presentation()
    original = deepcopy(p)
    view.update(p)
    view._relayout()
    assert p == original
    p["players"][0]["cards"][0]["rank"] = "2"
    assert view._last_snapshot == original


def snapshot():
    return {"seq": 8, "source_profile": "Profile", "source_mode": "individual",
            "source_resolution": [1000, 700], "round": 2, "shoe_id": "shoe-a",
            "card_groups": [{"seat_id": "J4", "is_hero": True,
                             "cards": [card(owner="mine"), card("b", "8", owner="mine")]}],
            "dealer_cards": [card("d1", "7", owner="dealer")],
            "count": {"per_rank": {"Ace": 2}, "running": 3, "true": 0.5,
                      "decks_remaining": 6, "cards_seen": 7, "count_uncertain": False},
            "error": None, "rounds": 0, "bet": "old bet", "metrics": {"cycle_ms": 0},
            "activity": "waiting", "cutting_card_seen": False}


def gui(view=None):
    g = SimpleNamespace(table=view or SimpleNamespace(remember_snapshot=Mock(), update=Mock()),
        mode_var=Variable("Individual"), profile_var=Variable("Profile"), _app_mode="individual",
        _applied_mode_context=("Profile", "individual", (1000, 700)), _source_configured=True,
        monitor=SimpleNamespace(width=1000, height=700), _last_seq=-1, _last_table_signature=None,
        controller=SimpleNamespace(running=True, engine=SimpleNamespace(get_snapshot=Mock(return_value=snapshot()))),
        executor=SimpleNamespace(step=Mock()), _render_executor=Mock(), _sync_start_button=Mock(),
        _render=Mock(), _poll_snapshot=Mock(), after=Mock(), _detection_action=None, round_overlay=Mock(),
        log_manager=Mock(), _sync_money_entries=Mock(), hud=None,
        counter_vars={"Ace": Variable()}, info_vars={key: Variable() for key in
            ("round", "phase", "running", "true", "decks", "seen", "bet", "behind", "pnl")},
        sidebet_rows={}, guardrail_banner=Widget(), anchor_badge=Widget(),
        reshuffle_badge=Widget(), paytable_warning=Widget(), set_status=Mock(), metrics_var=Variable())
    return g


def poll(g):
    GUI._poll_snapshot(g)
    g.log_manager.exception.assert_not_called()


def test_render_passes_presentation_preserving_statistics_money_and_minimesa(view):
    g = gui(view)
    g.hud = Mock()
    snap = snapshot()
    original = deepcopy(snap)
    spy = Mock(wraps=view.update)
    view.update = spy
    GUI._render(g, snap)
    p = spy.call_args.args[0]
    assert p == build_table_presentation(snap, selected_mode="individual", running=True)
    assert "seats" not in p and len(p["players"]) == 1
    assert g.counter_vars["Ace"].get() == "2"
    assert g.info_vars["running"].get() == "+3" and g.info_vars["seen"].get() == "7"
    assert g.info_vars["true"].get() == "+0.5"
    g._sync_money_entries.assert_called_once_with(snap)
    g.round_overlay.update_from_presentation.assert_called_once_with(
        p, running=True, metrics={key: snap["count"][key] for key in ("running", "true", "count_uncertain")})
    assert snap == original
    g.controller.engine.get_snapshot.assert_not_called()


def test_running_change_without_new_seq_stops_table_but_preserves_overlay_flow(view):
    g = gui(view)
    poll(g)
    assert len(view.seats[0]["cards"]) == 2
    g.controller.running = False
    poll(g)
    assert view.status_label.cget("text") == "Detección detenida"
    assert view.seats[0]["cards"] == [] and view.dealer["cards"] == []
    assert g._render.call_count == 1
    assert g.round_overlay.update_from_presentation.call_count == 2
    g.round_overlay.update_from_presentation.assert_called_with(
        view._last_snapshot, running=False, metrics={"running": 3, "true": .5, "count_uncertain": False})
    assert g.executor.step.call_count == 2


def test_unchanged_poll_builds_once_and_resumes_with_same_sequence(view, monkeypatch):
    g = gui(view)
    builder = Mock(wraps=build_table_presentation)
    monkeypatch.setattr(gui_module, "build_table_presentation", builder)
    for _ in range(3):
        poll(g)
    assert builder.call_count == 1
    g.controller.running = False
    poll(g)
    g.controller.running = True
    poll(g)
    assert builder.call_count == 3 and len(view.seats[0]["cards"]) == 2
    assert g.controller.engine.get_snapshot.call_count == 5


@pytest.mark.parametrize("field,bad", [("source_profile", "Other"), ("source_mode", "multiplayer"),
                                       ("source_resolution", [800, 600])])
def test_existing_provenance_filter_clears_main_table_without_passing_foreign_cards(view, field, bad):
    g = gui(view)
    poll(g)
    snap = g.controller.engine.get_snapshot.return_value
    snap[field] = bad
    poll(g)
    assert view.seats[0]["cards"] == [] and view.dealer["cards"] == []
    assert "Sin lectura válida" in view.status_label.cget("text")
    g.round_overlay.update_from_presentation.assert_called_with(view._last_snapshot, running=True, metrics={})
    g.executor.step.assert_called_with(None)
    assert g._render.call_count == 1


def test_no_valid_snapshot_then_recovery_with_same_seq_updates_table(view):
    g = gui(view)
    valid = g.controller.engine.get_snapshot.return_value
    poll(g)
    g.controller.engine.get_snapshot.return_value = None
    poll(g)
    assert view.seats[0]["cards"] == []
    g.controller.engine.get_snapshot.return_value = valid
    poll(g)
    assert len(view.seats[0]["cards"]) == 2


def test_calibration_invalidation_and_mode_change_clear_old_cards(view):
    g = gui(view)
    poll(g)
    g._source_configured = False
    poll(g)
    assert "calibrar" in view.status_label.cget("text")
    assert view.seats[0]["cards"] == []
    g.mode_var.set("Multijugador")
    poll(g)
    assert view.seats == [] and "dinámica pendiente" in view.mode_label.cget("text")


@pytest.mark.parametrize("attribute,value", [
    ("_applied_mode_context", ("Other", "individual", (1000, 700))),
    ("_applied_mode_context", ("Profile", "individual", (800, 600))),
    ("_source_configured", False),
])
def test_visual_signature_notices_configuration_changes(view, attribute, value):
    g = gui(view)
    poll(g)
    setattr(g, attribute, value)
    poll(g)
    assert view.seats[0]["cards"] == []


def test_render_uses_selected_mode_instead_of_historical_seat_assumptions(view):
    g = gui(view)
    g.mode_var.set("Multijugador")
    GUI._render(g, snapshot())
    assert view._last_snapshot["mode"] == "multiplayer"
    assert view._last_snapshot["status"] == "source_mismatch"
    assert view.seats == [] and view.dealer["cards"] == []


def test_table_render_does_not_change_counters_or_hidden_variables(view):
    g = gui(view)
    for variable in list(g.counter_vars.values()) + list(g.info_vars.values()):
        variable.set("preserved")
    snap = snapshot()
    original = deepcopy(snap)
    GUI._render_table(g, snap)
    assert snap == original
    assert all(var.get() == "preserved" for var in list(g.counter_vars.values()) + list(g.info_vars.values()))
    g.controller.engine.get_snapshot.assert_not_called()
    g._sync_money_entries.assert_not_called()


def test_gui_constructs_main_table_without_connecting_index_corrections(view, monkeypatch):
    owner = SimpleNamespace(focus_set=Mock())
    constructor = Mock(wraps=TableView)
    monkeypatch.setattr(gui_module, "TableView", constructor)
    GUI._build_table(owner)
    assert len(constructor.call_args.args) == 1
    assert constructor.call_args.kwargs == {}
    owner.table.update(presentation())
    assert all(not widget.bindings.get("<Button-1>")
               for hand in [owner.table.dealer] + owner.table.seats
               for widget in owner.table._hand_widgets(hand))
    owner.table.canvas.bindings["<Button-1>"](SimpleNamespace())
    owner.focus_set.assert_called_once()


def test_invalid_selected_mode_does_not_fall_back_to_previous_snapshot(view):
    g = gui(view)
    poll(g)
    g.mode_var.set("seven_seats")
    poll(g)
    assert view._last_snapshot["mode"] is None
    assert view._last_snapshot["status"] == "needs_calibration"
    assert view.seats == [] and view.dealer["cards"] == []


def test_preload_preserves_incremental_batches_and_cancels_old_generation(view):
    view.preload_images()
    generation = view._preload_gen
    assert len(view.canvas.jobs) == 1
    assert view.canvas.jobs[1][0] == 15
    old_callback = view.canvas.jobs[1][1]
    view.preload_images()
    assert view._preload_gen == generation + 1
    photos = table_module.ImageTk.PhotoImage.call_count
    old_callback()
    assert table_module.ImageTk.PhotoImage.call_count == photos
