"""Unit tests for card latch persistence, anti-mutation, and mini-table overlay click workflow.

Requirements covered:
1. Ruido en 1 solo frame no entra prematuramente (sin confirmar ni sumar a Hi-Lo).
2. Consenso temporal -> LOCKED (consenso por frames o mayoría ponderada >= 65%, cuenta exactamente una vez).
3. Hard Latch anti-desaparición (30 frames de oclusión consecutivos mantienen la carta en CardMemory,
   Engine, TablePresentation, TableView y RoundOverlay hasta New Round o New Shoe).
4. Anti-mutación (una vez LOCKED como '8', frames ruidosos con '3' no mutan el rango salvo edición manual).
5. Clic en RoundOverlay (Mini Mesa) y CardPicker 1-click (delta < 5px abre CardPicker con topmost=True,
   y pulsar rango '10' o 'A' invoca apply_correction, actualiza Running Count y refresca la vista).
"""
from copy import deepcopy
import math
from types import MethodType, SimpleNamespace
from unittest.mock import Mock
import pytest

from lib.common import constants
from lib.interfaces import table_view as table_module, modern_gui as gui_module, round_overlay as overlay_module
from lib.interfaces.round_overlay import MiniTableOverlay
from lib.interfaces.card_picker import CardPicker
from lib.interfaces.table_view import TableView
from lib.logic.card_layout import CardLayout
from lib.logic.card_memory import CardMemory, RememberedCard, CardState, CorrectionResult
from lib.logic.counting import CardCounter
from lib.logic.engine import DetectionEngine
from lib.logic.table_presentation import build_table_presentation
from tests.test_individual_table_view_unit import Widget as FakeTableViewWidget


# ---------------------------------------------------------------------------
# Test Fixtures & Lightweight Mocks
# ---------------------------------------------------------------------------

class FakeCanvas:
    """Headless Canvas mock recording items, tags, and coordinates."""
    def __init__(self):
        self.items = {}
        self.bindings = {}
        self.tags = {}
        self.serial = 0
        self.draws = 0

    def delete(self, tag):
        self.items.clear()
        self.bindings.clear()
        self.tags.clear()
        self.draws += 1

    def _create(self, kind, coords, options):
        self.serial += 1
        tags = options.get("tags", ())
        if isinstance(tags, str):
            tags = (tags,)
        self.items[self.serial] = {"kind": kind, "coords": list(coords), "tags": tuple(tags), **options}
        self.tags[self.serial] = tuple(tags)
        return self.serial

    def create_rectangle(self, *coords, **options):
        return self._create("rectangle", coords, options)

    def create_text(self, *coords, **options):
        return self._create("text", coords, options)

    def tag_bind(self, tag, event, callback):
        self.bindings[(tag, event)] = callback

    def coords(self, item, *new_coords):
        if new_coords:
            self.items[item]["coords"] = list(new_coords)
        return list(self.items.get(item, {}).get("coords", [0, 0, 0, 0]))

    def itemconfigure(self, item, **options):
        if item in self.items:
            self.items[item].update(options)

    def find_withtag(self, tag):
        if tag == "current":
            return [self.serial] if self.serial in self.items else []
        return [item_id for item_id, data in self.items.items() if tag in data.get("tags", ())]

    def gettags(self, item):
        return self.tags.get(item, ())

    def texts(self):
        return [item.get("text") for item in self.items.values() if item["kind"] == "text"]


class FakeWindow:
    """Headless Toplevel mock for overlay and dialog positioning."""
    def __init__(self, x=100, y=200, width=1000, height=310):
        self._x = x
        self._y = y
        self._width = width
        self._height = height
        self._exists = True
        self.attributes_dict = {"-topmost": True}
        self.last_geometry = None
        self.lift_called = False
        self.focus_force_called = False

    def winfo_exists(self):
        return self._exists

    def destroy(self):
        self._exists = False

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def winfo_rootx(self):
        return self._x

    def winfo_rooty(self):
        return self._y

    def geometry(self, geom=None):
        if geom is not None:
            self.last_geometry = geom
        return f"{self._width}x{self._height}+{self._x}+{self._y}"

    def attributes(self, key=None, value=None):
        if key is not None and value is not None:
            self.attributes_dict[key] = value
        elif key is not None:
            return self.attributes_dict.get(key)
        return self.attributes_dict

    def lift(self):
        self.lift_called = True

    def focus_force(self):
        self.focus_force_called = True

    def update_idletasks(self):
        pass

    def deiconify(self):
        pass

    def withdraw(self):
        pass

    def state(self):
        return "normal"

    def after(self, ms, func, *args):
        return "fake_job_id"

    def after_cancel(self, job_id):
        pass


def make_card_pred(name="8", cx=80.0, cy=100.0, owner="mine", confidence=0.95, width=30.0, height=45.0, **kwargs):
    """Factory for model detection bounding boxes."""
    return {
        "name": name,
        "class": name,
        "cx": float(cx),
        "cy": float(cy),
        "width": float(width),
        "height": float(height),
        "confidence": float(confidence),
        "owner": owner,
        **kwargs,
    }


def make_standard_layout():
    """Standard 1000x700 layout with hero at seat 0."""
    return CardLayout(
        mode="individual",
        resolution=(1000, 700),
        table=None,
        mine=((400, 450), (600, 650)),
        dealer=((450, 50), (550, 150)),
    )


def make_fake_overlay(on_correct_card=None):
    """Instantiate a headless MiniTableOverlay without real Tk window dependencies."""
    overlay = object.__new__(MiniTableOverlay)
    overlay.canvas = FakeCanvas()
    overlay.win = FakeWindow()
    overlay.parent = None
    overlay.on_correct_card = on_correct_card
    overlay.identity_corrections_enabled = callable(on_correct_card)
    overlay.on_close = None
    overlay.on_preferences_changed = None
    overlay.opacity = 0.88
    overlay.editing = False
    overlay.locked = False
    overlay.enabled = True
    overlay._capture_hidden = False
    overlay.affinity_ok = False
    overlay.hwnd = None
    overlay._last_state = None
    overlay._metrics = {}
    overlay._drawn_cards = []
    overlay._items = []
    overlay._item_pools = {"rectangle": [], "text": []}
    overlay._item_pool_cursors = {"rectangle": 0, "text": 0}
    overlay._item_cursor = 0
    overlay._running = True
    overlay._selected_id = None
    overlay._selected_round = None
    overlay._selected_suit = None
    overlay._selected_ref = None
    overlay._selected_expected_name = None
    overlay._notice = ""
    overlay._active = True
    overlay._drag_origin = None
    overlay._pointer_start = None
    overlay._pressed_identity = None

    for method in ("_draw", "_draw_card", "_draw_group", "_draw_hand", "_text", "_rect",
                   "_item", "_identity_at_pointer", "_pointer_press", "_pointer_release",
                   "_dispatch_identity", "_selection_exists", "_remember_pressed_identity",
                   "_show_window", "update_from_presentation"):
        if hasattr(MiniTableOverlay, method):
            setattr(overlay, method, MethodType(getattr(MiniTableOverlay, method), overlay))
    return overlay


def make_fake_table_view(monkeypatch):
    """Instantiate TableView with mocked widgets and image loaders."""
    for name in ("Canvas", "Label", "Frame", "Button"):
        monkeypatch.setattr(table_module.tk, name, FakeTableViewWidget)
    monkeypatch.setattr(table_module.scaling, "on_change", Mock())
    monkeypatch.setattr(table_module.scaling, "size", lambda size: tuple(size))
    monkeypatch.setattr(table_module.scaling, "px", lambda val: val)
    monkeypatch.setattr(table_module.Image, "open", Mock())
    monkeypatch.setattr(table_module.ImageTk, "PhotoImage", Mock(return_value=object()))
    return TableView(None)


# ---------------------------------------------------------------------------
# Requirement 1: Ruido en 1 solo frame no entra prematuramente
# ---------------------------------------------------------------------------

class TestSingleFrameNoiseRejection:
    """Detections seen for only 1 noisy frame must stay unconfirmed and not affect Hi-Lo."""

    def test_single_frame_noise_does_not_count_or_lock_card_memory(self):
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=4)

        # 1 frame with a +1 rank ('5')
        memory.observe([make_card_pred("5", cx=80.0, cy=100.0, owner="mine", confidence=0.95)], 1.0)

        assert counter.cards_seen == 0
        assert counter.running_count == 0
        assert counter.count_uncertain is True

        assert len(memory.cards) == 1
        card = list(memory.cards.values())[0]
        assert card.counted is False
        assert card.locked is False
        assert card.is_locked is False
        assert card.state == "pending"
        assert card.hits == 1
        assert card.valid_observations == 1
        assert memory.pending is True

    def test_insufficient_frames_below_consensus_remain_pending(self):
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=4)

        # 3 consecutive frames (below confirmations=4, and dt=0.200 < 0.350s)
        for t in (1.0, 1.1, 1.2):
            memory.observe([make_card_pred("King", cx=100.0, cy=120.0, owner="mine", confidence=0.90)], t)

        assert counter.cards_seen == 0
        assert counter.running_count == 0
        card = list(memory.cards.values())[0]
        assert card.counted is False
        assert card.locked is False
        assert card.state == "pending"

    def test_engine_single_frame_noise_does_not_mutate_count_or_hands(self):
        engine = DetectionEngine(persist=False)
        engine._replace_card_registry(enabled=True)
        engine.card_layout = make_standard_layout()
        engine.my_seats = {0}

        # 1 noisy frame at player seat
        noisy_pred = {"class": "5", "cx": 480.0, "cy": 500.0, "width": 30.0, "height": 45.0,
                      "confidence": 0.85, "owner": "mine"}
        engine._observe_registry([noisy_pred])

        assert engine.counter.cards_seen == 0
        assert engine.counter.running_count == 0
        assert len(engine.seats[0].cards) == 0

        engine.publish_snapshot()
        snap = engine.get_snapshot()
        assert snap["reading"]["count_pending"] is True

        p = build_table_presentation(snap, selected_mode="individual", running=True)
        # Pending card in presentation must NOT have a confirmed rank or count
        cards = p["players"][0]["cards"]
        assert all(c.get("rank") is None for c in cards)
        assert all(c.get("state") == "pending" for c in cards)
        assert all(not c.get("counted") for c in cards)


# ---------------------------------------------------------------------------
# Requirement 2: Consenso temporal -> LOCKED
# ---------------------------------------------------------------------------

class TestTemporalConsensusToLocked:
    """Accumulating consensus transitions the card to LOCKED and updates Hi-Lo exactly once."""

    def test_temporal_consensus_by_min_frames_locks_and_counts_exactly_once(self):
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=4)
        box = make_card_pred("5", cx=80.0, cy=100.0, owner="mine", confidence=0.95)

        # Feed frames 1 through 4
        memory.observe([box], 1.0)
        assert counter.running_count == 0
        memory.observe([box], 1.1)
        assert counter.running_count == 0
        memory.observe([box], 1.2)
        assert counter.running_count == 0

        # Frame 4 reaches min_consensus_frames (4)
        memory.observe([box], 1.3)
        card = list(memory.cards.values())[0]

        assert card.counted is True
        assert card.locked is True
        assert card.is_locked is True
        assert card.state == "locked"
        assert card.state == "confirmed"  # CardState equivalence
        assert counter.cards_seen == 1
        assert counter.running_count == 1  # 5 is +1 in Hi-Lo
        assert counter.count_uncertain is False

        # Additional repeated frames must NOT increment the counter again
        for t in range(5, 10):
            memory.observe([box], float(t))
        assert counter.cards_seen == 1
        assert counter.running_count == 1

    def test_temporal_consensus_by_time_window_and_confidence_majority(self):
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=4)

        # Observation 1 at t=1.000s
        box10 = make_card_pred("10", cx=150.0, cy=100.0, owner="dealer", confidence=0.95)
        memory.observe([box10], 1.000)
        assert counter.running_count == 0

        # Observation 2 at t=1.400s (dt=0.400s >= 0.350s, valid_observations=2, majority=100% >= 65%)
        memory.observe([box10], 1.400)
        card = list(memory.cards.values())[0]

        assert card.counted is True
        assert card.locked is True
        assert card.state == "locked"
        assert counter.cards_seen == 1
        assert counter.running_count == -1  # 10 is -1 in Hi-Lo

    def test_temporal_consensus_with_spurious_minority_votes_wins_majority(self):
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=4)

        # 3 high-confidence observations for Ace, 1 low-confidence spurious observation for 7
        memory.observe([make_card_pred("Ace", cx=80.0, cy=100.0, owner="mine", confidence=0.90)], 1.0)
        memory.observe([make_card_pred("Ace", cx=80.0, cy=100.0, owner="mine", confidence=0.90)], 1.1)
        memory.observe([make_card_pred("7", cx=80.0, cy=100.0, owner="mine", confidence=0.20)], 1.2)
        memory.observe([make_card_pred("Ace", cx=80.0, cy=100.0, owner="mine", confidence=0.90)], 1.4)

        card = list(memory.cards.values())[0]
        # Ace total weight = 2.7, 7 total weight = 0.2 -> Ace majority = 2.7 / 2.9 = ~93% >= 65%
        assert card.rank == "Ace"
        assert card.counted is True
        assert card.locked is True
        assert card.state == "locked"
        assert counter.running_count == -1  # Ace is -1 in Hi-Lo


# ---------------------------------------------------------------------------
# Requirement 3: Hard Latch anti-desaparición (30 frames de oclusión)
# ---------------------------------------------------------------------------

class TestHardLatchPersistenceAcross30OccludedFrames:
    """A LOCKED card must remain intact across 30 occluded frames in Memory, Engine, View and Overlay."""

    def test_hard_latch_30_occluded_frames_persists_in_card_memory(self):
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)

        hero_box = make_card_pred("8", cx=80.0, cy=100.0, owner="mine", confidence=0.95)
        dealer_box = make_card_pred("10", cx=200.0, cy=100.0, owner="dealer", confidence=0.95)

        # Confirm both cards to LOCKED
        memory.observe([hero_box, dealer_box], 1.0)
        memory.observe([hero_box, dealer_box], 2.0)

        hero_card = next(c for c in memory.cards.values() if c.owner == "mine")
        assert hero_card.rank == "8"
        assert hero_card.counted is True
        assert hero_card.is_locked is True
        assert counter.running_count == -1  # 8 is 0, 10 is -1

        hero_id = hero_card.id

        # Feed 30 consecutive frames where hero card is NOT detected (e.g. covered/occluded)
        for f in range(3, 33):
            memory.observe([dealer_box], float(f))

        # Hero card must still be in memory, counted, active, with rank '8' and hard locked
        assert hero_id in memory.cards
        persisted = memory.cards[hero_id]
        assert persisted.rank == "8"
        assert persisted.counted is True
        assert persisted.status == "active"
        assert persisted.is_locked is True
        assert persisted.state == "locked"  # Hard latch guarantees state stays locked/confirmed
        assert counter.running_count == -1  # count preserved intact

        # Snapshot contains the occluded locked card
        snap = memory.snapshot()
        assert any(c["id"] == hero_id and c["rank"] == "8" and c["counted"] is True for c in snap)

    def test_hard_latch_30_occluded_frames_persists_in_engine_presentation_tableview_overlay(self, monkeypatch):
        table_view = make_fake_table_view(monkeypatch)
        overlay = make_fake_overlay()

        engine = DetectionEngine(persist=False)
        engine._replace_card_registry(enabled=True)
        engine.card_layout = make_standard_layout()
        engine.my_seats = {0}

        hero_pred = {"class": "8", "cx": 480.0, "cy": 500.0, "width": 30.0, "height": 45.0,
                     "confidence": 0.95, "owner": "mine"}
        dealer_pred = {"class": "10", "cx": 500.0, "cy": 100.0, "width": 30.0, "height": 45.0,
                       "confidence": 0.95, "owner": "dealer"}

        # Lock cards
        engine._observe_registry([hero_pred, dealer_pred])
        engine._observe_registry([hero_pred, dealer_pred])
        engine.publish_snapshot()

        hero_id = next(c.id for c in engine.card_registry.cards.values() if c.owner == "mine")

        # 30 consecutive frames with hero card missing
        for f in range(4, 34):
            engine._observe_registry([dealer_pred])

        engine.publish_snapshot()
        snap = engine.get_snapshot()

        # 1. Engine & Snapshot persistence
        assert len(engine.seats[0].cards) == 1
        assert engine.seats[0].cards[0]["name"] == "8"
        assert engine.seats[0].cards[0]["id"] == hero_id
        assert any(c["id"] == hero_id and c["status"] == "active" for c in snap["card_registry"])
        assert snap["card_groups"][0]["cards"][0]["rank"] == "8"
        assert snap["card_groups"][0]["cards"][0]["card_id"] == hero_id

        # 2. TablePresentation persistence
        presentation = build_table_presentation(snap, selected_mode="individual", running=True)
        assert len(presentation["players"][0]["cards"]) == 1
        pres_card = presentation["players"][0]["cards"][0]
        assert pres_card["card_id"] == hero_id
        assert pres_card["rank"] == "8"

        # 3. TableView persistence
        table_view.update(presentation)
        assert len(table_view.seats[0]["cards"]) == 1
        assert table_view.seats[0]["cards"][0].card_data["card_id"] == hero_id
        assert table_view.seats[0]["cards"][0].card_data["rank"] == "8"

        # 4. Mini Mesa (RoundOverlay) persistence
        overlay.update_from_presentation(presentation, running=True)
        assert len(overlay._drawn_cards) == 2  # dealer 10 + hero 8
        drawn_hero = next(d for d in overlay._drawn_cards if d["card"]["card_id"] == hero_id)
        assert drawn_hero["card"]["rank"] == "8"

    def test_hard_latch_cleared_only_on_new_round_or_new_shoe(self):
        engine = DetectionEngine(persist=False)
        engine._replace_card_registry(enabled=True)
        engine.card_layout = make_standard_layout()
        engine.my_seats = {0}

        pred = {"class": "8", "cx": 480.0, "cy": 500.0, "width": 30.0, "height": 45.0,
                "confidence": 0.95, "owner": "mine"}
        dealer_pred = {"class": "10", "cx": 500.0, "cy": 100.0, "width": 30.0, "height": 45.0,
                       "confidence": 0.95, "owner": "dealer"}

        engine._observe_registry([pred, dealer_pred])
        engine._observe_registry([pred, dealer_pred])

        assert len(engine.card_registry.cards) == 2
        assert len(engine.seats[0].cards) == 1

        # New Round clears active table cards and moves them to archive
        engine.new_round()
        assert len(engine.card_registry.cards) == 0
        assert len(engine.card_registry.archive) == 2
        assert len(engine.seats[0].cards) == 0

        # Next round setup and New Shoe reset
        engine._observe_registry([pred, dealer_pred])
        engine._observe_registry([pred, dealer_pred])
        assert engine.counter.cards_seen == 4  # 2 from round 1, 2 from round 2

        engine.reset_shoe()
        assert engine.counter.cards_seen == 0
        assert engine.counter.running_count == 0


# ---------------------------------------------------------------------------
# Requirement 4: Prueba anti-mutación
# ---------------------------------------------------------------------------

class TestAntiMutationLockedRankProtection:
    """Once LOCKED as '8', noisy frames claiming '3' must not mutate the rank; only manual edit can."""

    def test_locked_card_does_not_mutate_from_subsequent_noisy_detections(self):
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)

        box8 = make_card_pred("8", cx=80.0, cy=100.0, owner="mine", confidence=0.95)
        memory.observe([box8], 1.0)
        memory.observe([box8], 2.0)

        card = list(memory.cards.values())[0]
        assert card.rank == "8"
        assert card.is_locked is True
        assert card.counted is True
        assert counter.running_count == 0

        # Feed 20 frames with noisy rank '3' at the exact same location
        box3 = make_card_pred("3", cx=80.0, cy=100.0, owner="mine", confidence=0.99)
        for t in range(3, 23):
            memory.observe([box3], float(t))

        # Rank must stay '8' and running count unchanged
        assert card.rank == "8"
        assert card.is_locked is True
        assert card.last_model_rank == "3"
        assert counter.running_count == 0

    def test_anti_mutation_in_engine_pipeline(self):
        engine = DetectionEngine(persist=False)
        engine._replace_card_registry(enabled=True)
        engine.card_layout = make_standard_layout()
        engine.my_seats = {0}

        pred8 = {"class": "8", "cx": 480.0, "cy": 500.0, "width": 30.0, "height": 45.0,
                 "confidence": 0.95, "owner": "mine"}
        engine._observe_registry([pred8])
        engine._observe_registry([pred8])
        assert engine.counter.running_count == 0

        # Model hallucinates '3' in subsequent frames
        pred3 = {"class": "3", "cx": 480.0, "cy": 500.0, "width": 30.0, "height": 45.0,
                 "confidence": 0.99, "owner": "mine"}
        for _ in range(15):
            engine._observe_registry([pred3])

        engine.publish_snapshot()
        snap = engine.get_snapshot()

        assert engine.seats[0].cards[0]["name"] == "8"
        assert snap["card_groups"][0]["cards"][0]["rank"] == "8"
        assert engine.counter.running_count == 0

    def test_manual_user_correction_is_the_only_way_to_change_locked_rank(self):
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)

        box8 = make_card_pred("8", cx=80.0, cy=100.0, owner="mine", confidence=0.95)
        memory.observe([box8], 1.0)
        memory.observe([box8], 2.0)
        card = list(memory.cards.values())[0]

        # Explicit manual correction from '8' to '3'
        result = memory.apply_correction(card.id, 1, "set_rank", new_rank="3")
        assert result.ok is True
        assert result.reason == "rank_updated"
        assert result.rc_delta == 1  # 8 (0) -> 3 (+1) delta is +1

        assert card.rank == "3"
        assert card.manual is True
        assert card.state == "confirmed"
        assert counter.running_count == 1

        # Even if the detector later sends '8' again, the manual correction wins
        memory.observe([box8], 3.0)
        assert card.rank == "3"
        assert counter.running_count == 1


# ---------------------------------------------------------------------------
# Requirement 5: Clic en RoundOverlay y CardPicker 1-click
# ---------------------------------------------------------------------------

class TestOverlayClickAndCardPickerOneClickCorrection:
    """Clicking a card in MiniTableOverlay opens CardPicker, and 1-click rank updates Engine and views."""

    def test_round_overlay_pointer_click_delta_threshold(self):
        clicked = []
        overlay = make_fake_overlay(on_correct_card=lambda ident: clicked.append(ident))

        card_info = {
            "card_id": "shoe:1:1", "rank": "8", "suit": None, "state": "confirmed",
            "owner": "mine", "round_id": 1, "shoe_id": "shoe",
        }
        presentation = {
            "mode": "individual", "status": "ready", "round_id": 1, "shoe_id": "shoe",
            "dealer": {"cards": []},
            "players": [{"id": "hero", "label": "Yo", "is_hero": True, "cards": [card_info]}],
        }
        overlay.update_from_presentation(presentation, running=True)
        assert len(overlay._drawn_cards) == 1

        drawn = overlay._drawn_cards[0]
        target_identity = drawn["identity"]

        # Case A: Low pointer delta (delta = 2px < 5px) triggers on_correct_card
        press = SimpleNamespace(x=100, y=180, x_root=500, y_root=300)
        release_close = SimpleNamespace(x_root=502, y_root=300)

        overlay._remember_pressed_identity(target_identity)
        overlay._pointer_press(press)
        overlay._pressed_identity = target_identity
        overlay.canvas.find_withtag = lambda tag: [drawn["item"]] if tag == "current" else []
        overlay.canvas.gettags = lambda item: [drawn["tag"]]

        overlay._pointer_release(release_close)
        assert len(clicked) == 1
        assert clicked[0]["card_id"] == "shoe:1:1"

        # Case B: High pointer delta (delta = 15px >= 5px, drag movement) does NOT trigger callback
        clicked.clear()
        release_drag = SimpleNamespace(x_root=515, y_root=300)

        overlay._remember_pressed_identity(target_identity)
        overlay._pointer_press(press)
        overlay._pressed_identity = target_identity

        overlay._pointer_release(release_drag)
        assert len(clicked) == 0

    def test_overlay_click_opens_card_picker_with_topmost(self, monkeypatch):
        picker_instances = []

        class MockCardPicker:
            def __init__(self, parent, title, image_lookup, on_select, card_identity=None, **kwargs):
                self.parent = parent
                self.title = title
                self.on_select = on_select
                self.card_identity = card_identity
                self.attributes_dict = {"-topmost": True}
                self.lift_called = True
                self.focus_force_called = True
                picker_instances.append(self)

            def attributes(self, key, value=None):
                if value is not None:
                    self.attributes_dict[key] = value
                return self.attributes_dict.get(key)

        monkeypatch.setattr(gui_module, "CardPicker", MockCardPicker)

        # Build mock GUI controller and trigger identity click
        card_identity = {
            "card_id": "shoe:1:1", "round_id": 1, "shoe_id": "shoe",
            "profile_name": None, "mode": "individual", "resolution": None,
            "source_id": None, "source_ref": None,
        }
        presentation = {
            "round_id": 1, "shoe_id": "shoe", "mode": "individual", "status": "ready",
            "players": [{"id": "hero", "cards": [{"card_id": "shoe:1:1", "state": "confirmed"}]}],
            "dealer": {"cards": []},
        }

        mock_gui = SimpleNamespace(
            _table_presentation=presentation,
            table=SimpleNamespace(card_image=Mock()),
            set_status=Mock(),
            _card_correction_picker=None,
            _finish_identity_correction=Mock(),
            _clear_identity_picker=Mock(),
            round_overlay=SimpleNamespace(raise_blocked=False),
        )

        success = gui_module.ModernBlackjackGUI._on_identity_card_click(mock_gui, card_identity)
        assert success is True
        assert len(picker_instances) == 1
        picker = picker_instances[0]
        assert picker.card_identity["card_id"] == "shoe:1:1"
        assert picker.attributes("-topmost") is True

    def test_card_picker_1_click_rank_10_applies_correction_updates_rc_and_refreshes_view(self, monkeypatch):
        table_view = make_fake_table_view(monkeypatch)
        overlay = make_fake_overlay()

        engine = DetectionEngine(persist=False)
        engine._replace_card_registry(enabled=True)
        engine.card_layout = make_standard_layout()
        engine.my_seats = {0}

        hero_pred = {"class": "8", "cx": 480.0, "cy": 500.0, "width": 30.0, "height": 45.0,
                     "confidence": 0.95, "owner": "mine"}
        dealer_pred = {"class": "10", "cx": 500.0, "cy": 100.0, "width": 30.0, "height": 45.0,
                       "confidence": 0.95, "owner": "dealer"}

        # Lock cards: hero has '8' (Hi-Lo 0), dealer has '10' (Hi-Lo -1). Initial RC = -1
        engine._observe_registry([hero_pred, dealer_pred])
        engine._observe_registry([hero_pred, dealer_pred])
        engine.publish_snapshot()
        assert engine.counter.running_count == -1

        hero_card = next(c for c in engine.card_registry.cards.values() if c.owner == "mine")
        hero_id = hero_card.id

        # Setup CardPicker with the card's identity
        captured_correction = []
        def on_picker_select(action, rank, suit, identity):
            captured_correction.append((action, rank, suit, identity))
            result = engine.apply_correction(identity["card_id"], identity["round_id"], action,
                                             new_rank=rank, new_suit=suit)
            engine.publish_snapshot()
            return result

        picker = SimpleNamespace(
            _card_identity={"card_id": hero_id, "round_id": 1, "shoe_id": engine.counter.shoe_id,
                            "rank": "8", "suit": None},
            _chosen_suit=None,
            _chosen_rank="8",
            _refresh_rank_buttons=Mock(),
            _on_select=on_picker_select,
            _closed=False,
            destroy=Mock(),
            _on_close=None,
        )
        picker._close = MethodType(CardPicker._close, picker)
        picker._on_rank_button_click = MethodType(CardPicker._on_rank_button_click, picker)
        picker._choose_rank = MethodType(CardPicker._choose_rank, picker)
        picker._set_rank = MethodType(CardPicker._set_rank, picker)

        # 1-Click Fast Edit: Clicking button '10' invokes _on_rank_button_click("10")
        picker._on_rank_button_click("10")

        # Verify callback invoked and popup closed
        assert picker._closed is True
        assert len(captured_correction) == 1
        assert captured_correction[0][0] == "set_rank"
        assert captured_correction[0][1] == "10"

        # Hi-Lo Running Count: 8 was 0, now 10 is -1. Dealer was 10 (-1). New RC = -2
        assert engine.counter.running_count == -2

        # Verify view refresh in Presentation, TableView and Overlay
        snap = engine.get_snapshot()
        new_presentation = build_table_presentation(snap, selected_mode="individual", running=True)
        assert new_presentation["players"][0]["cards"][0]["rank"] == "10"

        table_view.update(new_presentation)
        assert table_view.seats[0]["cards"][0].card_data["rank"] == "10"

        overlay.update_from_presentation(new_presentation, running=True)
        drawn_hero = next(d for d in overlay._drawn_cards if d["card"]["card_id"] == hero_id)
        assert drawn_hero["card"]["rank"] == "10"

    def test_card_picker_1_click_ace_updates_count_and_refreshes_view(self, monkeypatch):
        table_view = make_fake_table_view(monkeypatch)
        overlay = make_fake_overlay()

        engine = DetectionEngine(persist=False)
        engine._replace_card_registry(enabled=True)
        engine.card_layout = make_standard_layout()
        engine.my_seats = {0}

        hero_pred = {"class": "8", "cx": 480.0, "cy": 500.0, "width": 30.0, "height": 45.0,
                     "confidence": 0.95, "owner": "mine"}
        engine._observe_registry([hero_pred])
        engine._observe_registry([hero_pred])
        engine.publish_snapshot()
        assert engine.counter.running_count == 0

        hero_card = list(engine.card_registry.cards.values())[0]
        hero_id = hero_card.id

        def on_picker_select(action, rank, suit, identity):
            engine.apply_correction(identity["card_id"], identity["round_id"], action, new_rank=rank, new_suit=suit)
            engine.publish_snapshot()

        picker = SimpleNamespace(
            _card_identity={"card_id": hero_id, "round_id": 1, "shoe_id": engine.counter.shoe_id,
                            "rank": "8", "suit": None},
            _chosen_suit=None,
            _chosen_rank="8",
            _refresh_rank_buttons=Mock(),
            _on_select=on_picker_select,
            _closed=False,
            destroy=Mock(),
            _on_close=None,
        )
        picker._close = MethodType(CardPicker._close, picker)
        picker._on_rank_button_click = MethodType(CardPicker._on_rank_button_click, picker)
        picker._choose_rank = MethodType(CardPicker._choose_rank, picker)
        picker._set_rank = MethodType(CardPicker._set_rank, picker)

        # 1-Click Fast Edit on 'Ace'
        picker._on_rank_button_click("Ace")

        assert picker._closed is True
        # Ace is -1 in Hi-Lo (changed from 8 which was 0) -> RC = -1
        assert engine.counter.running_count == -1

        snap = engine.get_snapshot()
        p = build_table_presentation(snap, selected_mode="individual", running=True)
        assert p["players"][0]["cards"][0]["rank"] == "Ace"

        table_view.update(p)
        assert table_view.seats[0]["cards"][0].card_data["rank"] == "Ace"

        overlay.update_from_presentation(p, running=True)
        drawn = next(d for d in overlay._drawn_cards if d["card"]["card_id"] == hero_id)
        assert drawn["card"]["rank"] == "Ace"
