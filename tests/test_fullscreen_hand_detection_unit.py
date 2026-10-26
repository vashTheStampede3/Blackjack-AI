"""Deterministic checks for fullscreen crops, physical card identity and display.

All images and model outputs here are synthetic. These tests do not load a model
or access a capture device.
"""
import numpy as np
import pytest

from lib.logic.card_layout import CardLayout
from lib.logic.card_evidence import CardEvidence
from lib.logic.card_memory import CardMemory
from lib.logic.counting import CardCounter
from lib.logic.table_inference import prepare_layout_rois, predict_layout
from lib.logic.table_presentation import build_table_presentation


def _rect(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _layout(mode="individual"):
    payload = {"mode": mode, "resolution": [1920, 1200],
               "mine": _rect(510, 850, 790, 1080),
               "dealer": _rect(800, 70, 1080, 310)}
    if mode == "multiplayer":
        payload["table"] = _rect(300, 400, 1620, 1120)
    return CardLayout.from_payload(payload, (1920, 1200))


class _DeterministicModel:
    hosted = False

    def __init__(self, output_by_role):
        self.output_by_role = output_by_role
        self.calls = []

    def predict(self, image, confidence, overlap):
        # Synthetic model returns a point local to each native crop.
        self.calls.append((image.copy(), confidence, overlap))
        role_index = len(self.calls) - 1
        return [dict(self.output_by_role[role_index])]


@pytest.mark.parametrize("mode,roles", [("individual", ["mine", "dealer"]),
                                         ("multiplayer", ["table", "mine", "dealer"])])
def test_full_resolution_uses_independent_native_rois_and_preserves_frame(mode, roles):
    zones = _layout(mode)
    # Distinct pixel values make crop origin/content verifiable without a model.
    frame = np.zeros((1200, 1920, 3), np.uint8)
    frame[:] = (3, 7, 11)
    frame[850:1080, 510:790] = (31, 41, 51)
    frame[70:310, 800:1080] = (61, 71, 81)
    if mode == "multiplayer":
        frame[400:1120, 300:1620] = (91, 101, 111)
        frame[850:1080, 510:790] = (31, 41, 51)
        frame[70:310, 800:1080] = (61, 71, 81)
    original = frame.copy()
    # Chosen interior points remain comfortably inside their corresponding ROI.
    role_points = {"table": (1500, 700), "mine": (650, 950), "dealer": (950, 180)}
    rois = prepare_layout_rois(frame, zones)
    assert [role for role, _, _ in rois] == roles
    model = _DeterministicModel([
        {"cx": role_points[r][0] - bounds[0], "cy": role_points[r][1] - bounds[1],
         "width": 24., "height": 36., "class": "8", "confidence": .97}
        for r, bounds, _image in rois
    ])
    predictions, _metadata = predict_layout(model, frame, zones, 55, 40)
    assert len(model.calls) == len(roles)
    assert all(call[1] <= 55 and call[2] > 40 for call in model.calls)
    assert all(image.shape[:2] != frame.shape[:2] for image, _, _ in model.calls)
    for role, bounds, crop in rois:
        x, y = role_points[role]
        assert np.array_equal(crop[y - bounds[1], x - bounds[0]],
                              frame[y, x])
    assert [(p["cx"], p["cy"], p["owner"]) for p in predictions] == [
        (*role_points[r], zones.owner_at(*role_points[r])) for r in roles
        if zones.owner_at(*role_points[r]) is not None
    ]
    assert np.array_equal(frame, original)


def _pred(name, cx, cy, *, owner="mine", suit="spades", width=30, height=44,
          confidence=.91):
    return {"name": name, "owner": owner, "suit": suit, "cx": float(cx),
            "cy": float(cy), "width": float(width), "height": float(height),
            "confidence": confidence}


def test_opposite_corner_6_9_pair_keeps_upper_left_nine_and_hilo_zero():
    frame = np.zeros((240, 400, 3), np.uint8)
    evidence = CardEvidence()
    # Same physical card's opposing indices: lower-right 6 is the inverted
    # alternative; geometry says the upper-left 9 is the canonical read.
    result = evidence.observe(frame, [
        _pred("9", 100, 100), _pred("6", 145, 165),
    ])
    assert [p["name"] for p in result] == ["9"]
    counter = CardCounter()
    memory = CardMemory(counter, confirmations=2)
    for now in (1., 2.):
        memory.observe(result, now)
    assert counter.cards_seen == 1
    assert counter.running_count == 0


def test_moderate_isolated_six_or_nine_stays_ambiguous():
    counter = CardCounter()
    memory = CardMemory(counter, confirmations=2)
    isolated = [_pred("6", 100, 100, confidence=.62)]
    memory.observe(isolated, 1.)
    memory.observe(isolated, 2.)
    assert counter.cards_seen == 0
    assert counter.count_uncertain


def test_neighbor_jack_and_nine_remain_two_physical_cards_with_total_19():
    counter = CardCounter()
    memory = CardMemory(counter, confirmations=2)
    # Large, overlapping detector boxes with only a short stepped offset.
    pair = [_pred("Jack", 100, 100, width=80, height=120),
            _pred("9", 120, 108, width=80, height=120)]
    memory.observe(pair, 1.)
    memory.observe(pair, 2.)
    assert len(memory.cards) == 2
    assert counter.cards_seen == 2 and counter.running_count == -1
    assert {card.rank for card in memory.cards.values()} == {"Jack", "9"}
    assert sum(10 if card.rank == "Jack" else int(card.rank)
               for card in memory.cards.values()) == 19


def test_overlapping_same_rank_detections_keep_two_physical_ids():
    counter = CardCounter()
    memory = CardMemory(counter, confirmations=2)
    pair = [_pred("8", 100, 100, width=80, height=120),
            _pred("8", 120, 108, width=80, height=120)]
    memory.observe(pair, 1.)
    memory.observe(pair, 2.)
    assert len(memory.cards) == 2
    assert len({card.id for card in memory.cards.values()}) == 2
    assert counter.cards_seen == 2


def test_two_dealer_face_cards_remain_distinct_and_count_as_twenty():
    counter = CardCounter()
    memory = CardMemory(counter, confirmations=2)
    pair = [_pred("Queen", 900, 130, owner="dealer"),
            _pred("King", 928, 130, owner="dealer")]
    memory.observe(pair, 1.)
    memory.observe(pair, 2.)
    assert len(memory.cards) == 2
    assert counter.cards_seen == 2 and counter.running_count == -2
    assert {card.rank for card in memory.cards.values()} == {"Queen", "King"}
    assert sum(10 for _card in memory.cards.values()) == 20


@pytest.mark.parametrize("canvas_height,dpi", [(220, 1.), (280, 1.), (400, 1.),
                                                (220, 1.5), (280, 1.5), (400, 1.5)])
def test_real_relayout_keeps_hero_and_dealer_cards_inside_short_canvas(
        monkeypatch, canvas_height, dpi):
    from PIL import Image
    from tests.test_individual_table_view_unit import Widget
    from lib.interfaces import table_view as table_module
    from lib.interfaces.table_view import TableView

    for name in ("Canvas", "Label", "Frame", "Button"):
        monkeypatch.setattr(table_module.tk, name, Widget)
    monkeypatch.setattr(table_module.scaling, "on_change", lambda _callback: None)
    monkeypatch.setattr(table_module.scaling, "size",
                        lambda size: tuple(round(value * dpi) for value in size))
    monkeypatch.setattr(table_module.scaling, "px", lambda value: round(value * dpi))
    monkeypatch.setattr(table_module.Image, "open",
                        lambda _path: Image.new("RGB", (300, 436), "white"))
    monkeypatch.setattr(table_module.ImageTk, "PhotoImage", lambda _image: object())

    table = TableView(None)
    table.canvas.width, table.canvas.height = 800, canvas_height
    pending_hero = {"card_id": "hero-pending", "rank": None, "state": "pending"}
    pending_dealer = {"card_id": "dealer-pending", "rank": None, "state": "pending"}
    table.update({"mode": "individual", "status": "reading", "message": "Leyendo",
                  "dealer": {"cards": [pending_dealer]},
                  "players": [{"id": "hero", "label": "Yo", "is_hero": True,
                               "cards": [pending_hero]}]})
    # Exercise the production geometry and image fitting at this size/DPI.
    table._relayout()

    assert table.seats[0]["name"].cget("text") == "Yo"
    assert table.dealer_title.cget("text") == "Crupier"
    for hand in (table.seats[0], table.dealer):
        card_widget = hand["cards"][0]
        assert card_widget.card_data["card_id"] in {"hero-pending", "dealer-pending"}
        x, y = card_widget.position["x"], card_widget.position["y"]
        card_height = card_widget._visual_size[1]
        assert 0 <= y and y + card_height <= canvas_height
        name_y = hand["name"].position["y"]
        assert 0 <= name_y < canvas_height


def test_pending_cards_keep_physical_ids_and_project_as_question_marks_for_both_hands():
    counter = CardCounter()
    memory = CardMemory(counter)
    memory.observe([_pred("9", 640, 960), _pred("Queen", 940, 180, owner="dealer")], 1.)
    assert len(memory.cards) == 2
    snapshot = {"source_mode": "individual", "source_resolution": [1920, 1200],
                "card_groups": [{"seat_id": "J1", "is_hero": True, "cards": [
                    {"card_id": card.id, "rank": card.rank, "state": card.state,
                     "owner": card.owner, "hand": 0} for card in memory.cards.values()
                    if card.owner == "mine"]}],
                "dealer_cards": [{"card_id": card.id, "rank": card.rank,
                                  "state": card.state, "owner": card.owner}
                                 for card in memory.cards.values() if card.owner == "dealer"]}
    view = build_table_presentation(snapshot, selected_mode="individual", running=True)
    assert view["players"][0]["label"] == "Yo"
    assert view["players"][0]["cards"][0]["card_id"]
    assert view["players"][0]["cards"][0]["rank"] is None
    assert view["dealer"]["cards"][0]["card_id"]
    assert view["dealer"]["cards"][0]["rank"] is None

    # Exercise TableView.update with lightweight widgets, without creating a
    # window, and verify both lower-height-visible hand labels and pending cards
    # reach the actual hand renderer.
    from lib.interfaces.table_view import TableView

    class _Widget:
        def __init__(self, text=""):
            self.text = text

        def config(self, **kwargs):
            self.text = kwargs.get("text", self.text)

    rendered = []
    table = object.__new__(TableView)
    table._preview_item = None
    table._mode = "individual"
    table.dealer = {"name": _Widget("Crupier"), "advice": _Widget(),
                    "cards": [], "card_keys": [], "rendered": []}
    table.seats = [{"id": "hero", "name": _Widget("Yo"), "advice": _Widget(),
                    "split_notice": _Widget(), "cards": [], "card_keys": [],
                    "rendered": [], "hand_titles": [], "is_hero": True}]
    table.status_label = _Widget()
    table.mode_label = _Widget()
    table._make_seats = lambda players=None: None
    table._update_hand = lambda hand, cards, presentation, dealer=False: rendered.append(
        (hand["name"].text, list(cards)))
    table._relayout = lambda: None
    table.remember_snapshot = lambda presentation: None
    table.update(view)
    assert [title for title, _ in rendered] == ["Crupier", "Yo"]
    assert all(cards and cards[0]["rank"] is None and cards[0]["card_id"]
               for _, cards in rendered)


def test_onnx_nms_preserves_overlapping_different_and_equal_ranks():
    from lib.logic.models import OnnxModel
    model = object.__new__(OnnxModel)
    model._names = {0: "J", 1: "9"}
    output = np.zeros((6, 12), np.float32)
    # J + 9 overlap strongly. A repeated proposal for J is still suppressed.
    for i, (x, y, label, score) in enumerate([(100, 100, 0, .96),
                                             (116, 108, 1, .95),
                                             (101, 100, 0, .80)]):
        output[:4, i] = (x, y, 80, 120)
        output[4+label, i] = score
    detections = model._decode(output, .35, .80, 1., 0, 0)
    assert [p["class"] for p in detections] == ["J", "9"]
    output[4:, :] = 0
    output[4, :2] = (.96, .95)
    output[0, 1] = 112  # 15% of a full card width; both cards must survive.
    detections = model._decode(output, .35, .80, 1., 0, 0)
    assert len(detections) == 2


def test_roi_weak_figure_is_visible_but_cannot_count_as_confirmed():
    zones = _layout()
    frame = np.zeros((1200, 1920, 3), np.uint8)
    rois = prepare_layout_rois(frame, zones)
    model = _DeterministicModel([
        {"class": "J", "confidence": .40, "cx": 100, "cy": 100,
         "width": 20, "height": 30},
        {"class": "Q", "confidence": .40, "cx": 100, "cy": 100,
         "width": 20, "height": 30}])
    result, _ = predict_layout(model, frame, zones, 55, 45)
    assert len(result) == 2 and all(p["ambiguous"] for p in result)
    counter = CardCounter()
    memory = CardMemory(counter, confirmations=1)
    for now in (1., 2., 3.):
        memory.observe([p | {"name": "Jack" if p["owner"] == "mine" else "Queen"}
                        for p in result], now)
    assert counter.cards_seen == 0 and counter.count_uncertain
    assert len(memory.cards) == 2 and all(c.id for c in memory.cards.values())


def test_engine_fullscreen_q_k_dealer_and_overlapped_j_nine_no_false_six(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from lib.common import constants
    from lib.logic.engine import DetectionEngine
    from lib.logic import region_profiles
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(constants.BETTING, "use_exact_edge", 0)
    for config in (constants.OCR, constants.PHASE, constants.ANCHORS, constants.TRAINING):
        monkeypatch.setitem(config, "enabled", 0)
    zones = _layout()
    region_profiles.set_card_layout(zones, profile="Fullscreen test")
    frame = np.zeros((1200, 1920, 3), np.uint8)
    rois = prepare_layout_rois(frame, zones)
    native = [dict(_pred("J", 630, 950), **{"class": "J"}),
              dict(_pred("9", 650, 958), **{"class": "9"}),
              dict(_pred("6", 695, 1023), **{"class": "6"}),
              dict(_pred("Q", 910, 180, owner="dealer"), **{"class": "Q"}),
              dict(_pred("K", 940, 180, owner="dealer"), **{"class": "K"})]
    class Model:
        hosted = False
        def predict(self, image, *_args):
            bounds = next((bounds for _, bounds, crop in rois if crop.shape == image.shape), None)
            if bounds is None:
                return []
            l, t, r, b = bounds
            return [dict(p, cx=p["cx"]-l, cy=p["cy"]-t) for p in native
                    if l <= p["cx"] < r and t <= p["cy"] < b]
    model = Model()
    provider = SimpleNamespace(backend_name="synthetic fullscreen", players_model=lambda: model,
        dealer_model=lambda: model, has_hosted=lambda: False, refresh=lambda **kw: False)
    engine = DetectionEngine(provider=provider, persist=False, log=lambda *a, **kw: None)
    try:
        engine.set_monitor(SimpleNamespace(width=1920, height=1200, x=0, y=0),
                           profile="Fullscreen test", mode="individual")
        engine.capture.grab_bgr = lambda: frame.copy()
        engine._frame_unchanged = lambda _frame: False
        for _ in range(max(3, constants.EXTRA_CARD_CONFIRM_CYCLES + 1)):
            engine.run_cycle()
        snapshot = engine.get_snapshot()
        presentation = build_table_presentation(snapshot, selected_mode="individual", running=True)
        assert engine.counter.cards_seen == 4
        assert engine.counter.running_count == -3
        assert {c["rank"] for c in presentation["players"][0]["cards"]} == {"Jack", "9"}
        assert {c["rank"] for c in presentation["dealer"]["cards"]} == {"Queen", "King"}
        assert presentation["advice_available"]
        assert not snapshot["count"]["count_uncertain"]
        assert len({c["card_id"] for c in presentation["players"][0]["cards"]
                    + presentation["dealer"]["cards"]}) == 4
    finally:
        for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
            getattr(engine, name).shutdown(wait=True, cancel_futures=True)


def test_late_upper_corner_reuses_pending_lower_identity_and_resolves_uncertainty():
    counter = CardCounter()
    memory = CardMemory(counter, confirmations=2)
    lower = _pred("6", 145, 165, confidence=.62)
    memory.observe([lower], 1.)
    first_id = next(iter(memory.cards))
    pair = [_pred("9", 100, 100), lower]
    memory.observe(pair, 2.)
    memory.observe(pair, 3.)
    active = [card for card in memory.cards.values() if card.status == "active"]
    assert len(active) == 1 and active[0].id == first_id
    assert active[0].rank == "9" and active[0].state == "confirmed"
    assert counter.cards_seen == 1 and counter.running_count == 0
    assert not counter.count_uncertain
