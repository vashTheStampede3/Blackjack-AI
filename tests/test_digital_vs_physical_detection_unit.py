"""Unit tests for Etapa 8: Dual specialized detection for 2D digital cards vs physical cards.

Coverage:
1. Detection of 2D digital hands (clean 2D card sprites for ranks 2-10, J, Q, K, A,
   horizontal strips of 1 to 5 cards left-to-right, non-overlapping or slightly overlapping):
   - In dealer zone (in both 'individual' and 'multiplayer' modes).
   - In 'mine' zone (in 'individual' mode).
   - Verifying that neighbor cards in horizontal strips never collapse into a single card box in card_layout.
2. Hybrid multiplayer scenario:
   - Dealer zone: 2D digital card representations.
   - Table / Mine zones: physical cards (photographed on felt, possibly tilted or overlapped).
   - Verify proper surface routing: dealer -> digital (with physical fallback),
     table/mine -> physical in multiplayer; dealer/mine -> digital in individual.
3. Full integration with CardState("locked") and discarded_regions (from Etapa 7A commit 9a052cc):
   - Digital and physical cards reach locked/confirmed status via temporal consensus, add to Running Count once.
   - Once locked, digital and physical cards NEVER disappear nor mutate across frames even if undetected for 30+ frames.
   - If user manual correction is applied or discarded ("not_a_card"), discarded_regions prevents reviving.
   - Click hit-testing on RoundOverlay and TableView works seamlessly for both digital and physical card bboxes.
4. Synthetic training data generation:
   - Test synthetic digital card strip generation functionality in tools/prepare_training.py.
"""
from copy import deepcopy
import math
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pytest

from lib.common import constants
from lib.common.card_mappings import PLAYER_CLASS_MAP
from lib.interfaces import table_view as table_module, round_overlay as overlay_module
from lib.interfaces.round_overlay import MiniTableOverlay
from lib.interfaces.table_view import TableView
from lib.logic.app_modes import INDIVIDUAL, MULTIPLAYER
from lib.logic.card_evidence import CardEvidence
from lib.logic.card_layout import CardLayout, TableGroupTracker
from lib.logic.card_memory import CardMemory, RememberedCard, CardState, CorrectionResult
from lib.logic.counting import CardCounter
from lib.logic.table_inference import (
    SURFACE_DIGITAL,
    SURFACE_PHYSICAL,
    surface_for_role,
    route_surface_model,
    predict_layout_dual,
    prepare_layout_rois,
    predict_layout,
)
from lib.logic.table_presentation import build_table_presentation
from lib.logic.vision_labels import RANKS, canonical_rank
from tests.test_individual_table_view_unit import Widget as FakeTableViewWidget
from tools.prepare_training import (
    generate_digital_card_sprite,
    generate_digital_card_strip,
    format_yolo_labels,
    generate_synthetic_strip_dataset,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Headless Mocks
# ---------------------------------------------------------------------------

class FakeCanvas:
    """Headless Tk Canvas mock recording items, tags, and hitboxes."""
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
    """Headless Toplevel mock for window positioning."""
    def __init__(self, x=100, y=100, width=800, height=350):
        self._x = x
        self._y = y
        self._width = width
        self._height = height
        self._exists = True
        self.attributes_dict = {"-topmost": True}
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


class FakePointerEvent:
    def __init__(self, x, y, x_root=None, y_root=None):
        self.x = x
        self.y = y
        self.x_root = x if x_root is None else x_root
        self.y_root = y if y_root is None else y_root


class MockDetectorModel:
    """Configurable synthetic model returning predictable predictions for crops."""
    hosted = False

    def __init__(self, model_type="generic"):
        self.model_type = model_type
        self.calls = []
        self.canned_predictions = None

    def predict(self, image, confidence=50, overlap=45):
        self.calls.append({
            "shape": image.shape,
            "confidence": confidence,
            "overlap": overlap,
        })
        if self.canned_predictions is not None:
            return [dict(p) for p in self.canned_predictions]
        return []


def rect(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def make_layout(mode="individual"):
    """Create test layouts matching typical casino frame resolutions."""
    payload = {
        "mode": mode,
        "resolution": [1920, 1080],
        "dealer": rect(750, 40, 1170, 240),
        "mine": rect(700, 680, 1220, 960),
    }
    if mode == "multiplayer":
        payload["table"] = rect(150, 450, 1770, 1020)
    return CardLayout.from_payload(payload, (1920, 1080))


def make_fake_overlay(on_correct_card=None):
    """Instantiate a headless MiniTableOverlay without native Tk windowing."""
    overlay = object.__new__(MiniTableOverlay)
    overlay.canvas = FakeCanvas()
    overlay.win = FakeWindow()
    overlay.parent = None
    overlay.on_correct_card = on_correct_card
    overlay.identity_corrections_enabled = callable(on_correct_card)
    overlay.on_close = None
    overlay.on_preferences_changed = None
    overlay.opacity = 0.90
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


# ===========================================================================
# 1. Detection of 2D Digital Hands (Requirement 1)
# ===========================================================================

class TestDigitalHandsDetection:
    """Verifies detection of 2D digital hands (clean 2D card sprites for ranks
    2-10, J, Q, K, A, horizontal strips of 1 to 5 cards left-to-right, non-overlapping
    or slightly overlapping) in dealer zone and mine zone, without collapsing neighbor cards.
    """

    @pytest.mark.parametrize("rank", list(RANKS))
    def test_all_13_digital_ranks_sprites_generated_and_detected(self, rank):
        """Clean 2D card sprites for all 13 canonical ranks can be generated and identified."""
        sprite = generate_digital_card_sprite(rank, suit="H" if rank in ("A", "10") else "S", width=60, height=90)
        assert sprite.shape == (90, 60, 3)
        assert sprite.dtype == np.uint8
        # Sprite has non-trivial drawing (not just a flat color)
        assert np.std(sprite) > 5.0
        assert canonical_rank(rank) == rank

    @pytest.mark.parametrize("mode", ["individual", "multiplayer"])
    @pytest.mark.parametrize("hand_ranks", [
        ["A"],
        ["10", "K"],
        ["7", "8", "3"],
        ["4", "5", "6", "2"],
        ["9", "J", "Q", "A", "10"],
    ])
    def test_digital_hand_1_to_5_cards_in_dealer_zone(self, mode, hand_ranks):
        """Strips of 1 to 5 digital cards in dealer zone are correctly detected
        left-to-right with exact ranks in both individual and multiplayer modes.
        """
        layout = make_layout(mode)
        strip_img, annotations = generate_digital_card_strip(
            hand_ranks, card_size=(60, 90), spacing=12, overlap_pixels=0
        )
        assert len(annotations) == len(hand_ranks)

        # Place strip into dealer region in a 1920x1080 frame
        frame = np.full((1080, 1920, 3), 20, dtype=np.uint8)
        d_left, d_top, d_right, d_bottom = 750, 40, 1170, 240
        sh, sw = strip_img.shape[:2]
        frame[d_top + 10:d_top + 10 + sh, d_left + 15:d_left + 15 + sw] = strip_img

        # Configure mock model to return detections corresponding to strip annotations
        dealer_model = MockDetectorModel(model_type="digital_dealer")
        # In ROI crop coordinates:
        canned = []
        for ann in annotations:
            canned.append({
                "cx": 15 + ann["cx"],
                "cy": 10 + ann["cy"],
                "width": ann["width"],
                "height": ann["height"],
                "class": ann["rank"],
                "name": ann["rank"],
                "confidence": 0.96,
            })
        dealer_model.canned_predictions = canned

        physical_model = MockDetectorModel(model_type="physical")
        physical_model.canned_predictions = []

        predictions, meta = predict_layout_dual(
            frame, layout, confidence=50, overlap=45,
            digital_model=dealer_model, physical_model=physical_model
        )

        dealer_preds = [p for p in predictions if p["owner"] == "dealer"]
        assert len(dealer_preds) == len(hand_ranks)

        # Ensure left-to-right ordering: cx must be strictly monotonically increasing
        cxs = [p["cx"] for p in dealer_preds]
        assert cxs == sorted(cxs)
        for i in range(len(cxs) - 1):
            assert cxs[i + 1] > cxs[i]

        detected_ranks = [p["class"] for p in dealer_preds]
        assert detected_ranks == hand_ranks

    @pytest.mark.parametrize("hand_ranks", [
        ["K"],
        ["9", "9"],
        ["2", "3", "4"],
        ["A", "8", "6", "5"],
        ["10", "2", "3", "4", "A"],
    ])
    def test_digital_hand_1_to_5_cards_in_mine_zone_individual_mode(self, hand_ranks):
        """In individual mode, 1 to 5 digital cards in mine zone are detected left-to-right."""
        layout = make_layout("individual")
        strip_img, annotations = generate_digital_card_strip(
            hand_ranks, card_size=(60, 90), spacing=10, overlap_pixels=0
        )
        frame = np.full((1080, 1920, 3), 20, dtype=np.uint8)
        m_left, m_top, m_right, m_bottom = 700, 680, 1220, 960
        sh, sw = strip_img.shape[:2]
        frame[m_top + 10:m_top + 10 + sh, m_left + 15:m_left + 15 + sw] = strip_img

        digital_model = MockDetectorModel(model_type="digital")
        canned = []
        for ann in annotations:
            canned.append({
                "cx": 15 + ann["cx"],
                "cy": 10 + ann["cy"],
                "width": ann["width"],
                "height": ann["height"],
                "class": ann["rank"],
                "name": ann["rank"],
                "confidence": 0.95,
            })
        digital_model.canned_predictions = canned

        predictions, _ = predict_layout_dual(
            frame, layout, confidence=50, overlap=45,
            digital_model=digital_model, physical_model=None
        )

        mine_preds = [p for p in predictions if p["owner"] == "mine"]
        assert len(mine_preds) == len(hand_ranks)
        assert [p["class"] for p in mine_preds] == hand_ranks
        cxs = [p["cx"] for p in mine_preds]
        assert cxs == sorted(cxs)

    def test_neighbor_cards_in_horizontal_strip_never_collapse_no_overlap(self):
        """Verifies adjacent cards in a horizontal strip never collapse into a single box."""
        layout = make_layout("individual")
        strip_img, annotations = generate_digital_card_strip(
            ["10", "J", "Q"], card_size=(60, 90), spacing=12, overlap_pixels=0
        )
        assert len(annotations) == 3
        # Strict bounding box separation
        for i in range(len(annotations) - 1):
            assert annotations[i]["box"][2] < annotations[i + 1]["box"][0]

        # Feed detections through CardEvidence and CardMemory
        evidence = CardEvidence()
        preds = [{
            "cx": ann["cx"] + 750,
            "cy": ann["cy"] + 40,
            "width": ann["width"],
            "height": ann["height"],
            "name": ann["rank"],
            "class": ann["rank"],
            "owner": "dealer",
            "confidence": 0.95,
        } for ann in annotations]

        frame = np.full((1080, 1920, 3), 40, dtype=np.uint8)
        observed = evidence.observe(frame, preds)
        assert len(observed) == 3

        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)
        memory.observe(observed, now=1.0)
        # All 3 distinct cards are registered
        assert len(memory.cards) == 3
        card_ranks = sorted([c.rank for c in memory.cards.values()])
        assert card_ranks == ["10", "J", "Q"]

    def test_neighbor_cards_in_horizontal_strip_never_collapse_with_partial_overlap(self):
        """Verifies slightly overlapping digital cards (e.g. 15px overlap, ~25% card width)
        maintain distinct bounding boxes, identities and ranks without collapsing.
        """
        strip_img, annotations = generate_digital_card_strip(
            ["A", "9", "8"], card_size=(60, 90), spacing=0, overlap_pixels=15
        )
        assert len(annotations) == 3
        # Overlap exists: card 1 right edge > card 2 left edge
        assert annotations[0]["box"][2] > annotations[1]["box"][0]
        # But center distance is 45px, which is > 10% card width
        assert annotations[1]["cx"] - annotations[0]["cx"] == 45.0

        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)
        preds = [{
            "cx": ann["cx"] + 700,
            "cy": ann["cy"] + 680,
            "width": ann["width"],
            "height": ann["height"],
            "name": ann["rank"],
            "class": ann["rank"],
            "owner": "mine",
            "confidence": 0.94,
        } for ann in annotations]

        memory.observe(preds, now=1.0)
        assert len(memory.cards) == 3
        c_ranks = [c.rank for c in sorted(memory.cards.values(), key=lambda c: c.cx)]
        assert c_ranks == ["A", "9", "8"]

    def test_table_group_tracker_tracks_player_hand_strip_without_collapsing(self):
        """TableGroupTracker assigns all cards in the player's strip to the same seat
        while keeping all individual card records distinct.
        """
        tracker = TableGroupTracker()
        tracker.reset_for_round()

        cards = [
            RememberedCard(
                id=f"SHOE:1:{i}", rank=r, cx=800.0 + i * 45.0, cy=750.0,
                width=60.0, height=90.0, first_seen=1.0, last_seen=1.0,
                owner="mine", observed_owner="mine"
            ) for i, r in enumerate(["A", "7", "3"])
        ]
        groups = tracker.update_groups(cards)
        assert len(groups) == 1
        hero_group = groups[0]
        assert hero_group.is_hero
        assert len(hero_group.cards) == 3
        assert set(c.rank for c in hero_group.cards) == {"A", "7", "3"}


# ===========================================================================
# 2. Hybrid Multiplayer Scenario & Surface Routing (Requirement 2)
# ===========================================================================

class TestHybridMultiplayerSurfaceRouting:
    """Verifies surface routing across modes:
    - Dealer -> digital (with physical fallback)
    - Table / Mine -> physical in multiplayer
    - Dealer / Mine -> digital in individual
    """

    def test_surface_for_role_determination(self):
        """surface_for_role maps zones to digital vs physical based on mode."""
        # Individual mode
        assert surface_for_role("individual", "dealer") == SURFACE_DIGITAL
        assert surface_for_role("individual", "mine") == SURFACE_DIGITAL

        # Multiplayer mode
        assert surface_for_role("multiplayer", "dealer") == SURFACE_DIGITAL
        assert surface_for_role("multiplayer", "mine") == SURFACE_PHYSICAL
        assert surface_for_role("multiplayer", "table") == SURFACE_PHYSICAL

        # Unknown modes / roles raise ValueError
        with pytest.raises(ValueError):
            surface_for_role("unknown_mode", "dealer")
        with pytest.raises(ValueError):
            surface_for_role("individual", "table")
        with pytest.raises(ValueError):
            surface_for_role("multiplayer", "unknown_role")

    def test_route_surface_model_dispatch(self):
        """route_surface_model returns the appropriate model based on surface type."""
        d_model = MockDetectorModel("digital")
        p_model = MockDetectorModel("physical")

        # Multiplayer
        model, surface = route_surface_model(
            "multiplayer", "dealer", digital_model=d_model, physical_model=p_model
        )
        assert model is d_model
        assert surface == SURFACE_DIGITAL

        model, surface = route_surface_model(
            "multiplayer", "mine", digital_model=d_model, physical_model=p_model
        )
        assert model is p_model
        assert surface == SURFACE_PHYSICAL

        model, surface = route_surface_model(
            "multiplayer", "table", digital_model=d_model, physical_model=p_model
        )
        assert model is p_model
        assert surface == SURFACE_PHYSICAL

        # Individual
        model, surface = route_surface_model(
            "individual", "dealer", digital_model=d_model, physical_model=p_model
        )
        assert model is d_model
        assert surface == SURFACE_DIGITAL

        model, surface = route_surface_model(
            "individual", "mine", digital_model=d_model, physical_model=p_model
        )
        assert model is d_model
        assert surface == SURFACE_DIGITAL

    def test_dealer_digital_with_physical_fallback_when_digital_model_none(self):
        """When digital_model is None, dealer routes to physical_model as fallback."""
        p_model = MockDetectorModel("physical")
        model, surface = route_surface_model(
            "multiplayer", "dealer", digital_model=None, physical_model=p_model, fallback=True
        )
        assert model is p_model
        assert surface == "physical_fallback"

        # If fallback is explicitly disabled, returns None
        model, surface = route_surface_model(
            "multiplayer", "dealer", digital_model=None, physical_model=p_model, fallback=False
        )
        assert model is None
        assert surface == SURFACE_DIGITAL

    def test_dealer_digital_with_physical_fallback_when_digital_model_returns_empty(self):
        """In predict_layout_dual, if digital_model produces 0 predictions on dealer crop,
        fallback=True automatically invokes physical_model.
        """
        layout = make_layout("multiplayer")
        frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)

        digital_model = MockDetectorModel("digital")
        digital_model.canned_predictions = []  # digital model sees nothing

        physical_model = MockDetectorModel("physical")
        physical_model.canned_predictions = [{
            "cx": 100.0, "cy": 80.0, "width": 55.0, "height": 85.0,
            "class": "Q", "name": "Q", "confidence": 0.92,
        }]

        preds, meta = predict_layout_dual(
            frame, layout, confidence=50, overlap=45,
            digital_model=digital_model, physical_model=physical_model, fallback=True
        )

        dealer_preds = [p for p in preds if p["owner"] == "dealer"]
        assert len(dealer_preds) == 1
        assert dealer_preds[0]["class"] == "Q"
        assert dealer_preds[0]["surface"] == "physical_fallback"

    def test_dealer_digital_no_fallback_when_fallback_disabled(self):
        """When fallback=False and digital_model returns empty, physical model is not invoked."""
        layout = make_layout("multiplayer")
        frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)

        digital_model = MockDetectorModel("digital")
        digital_model.canned_predictions = []

        physical_model = MockDetectorModel("physical")
        physical_model.canned_predictions = [{
            "cx": 100.0, "cy": 80.0, "width": 55.0, "height": 85.0,
            "class": "Q", "name": "Q", "confidence": 0.92,
        }]

        preds, _ = predict_layout_dual(
            frame, layout, confidence=50, overlap=45,
            digital_model=digital_model, physical_model=physical_model, fallback=False
        )
        dealer_preds = [p for p in preds if p["owner"] == "dealer"]
        assert len(dealer_preds) == 0

    def test_hybrid_multiplayer_frame_dual_detection(self):
        """Full hybrid frame:
        - Dealer zone: 2D digital cards (A and K)
        - Mine zone: physical cards (10 and 8)
        - Table zone: physical other cards (5 and 6)
        Verifies dual models route appropriately, preserve surface tags, and detect 6 distinct cards.
        """
        layout = make_layout("multiplayer")
        frame = np.full((1080, 1920, 3), 25, dtype=np.uint8)

        # Build mock models
        digital_model = MockDetectorModel("digital")
        # Dealer ROI: 750, 40 -> width 420, height 200
        digital_model.canned_predictions = [
            {"cx": 80.0, "cy": 90.0, "width": 60.0, "height": 90.0, "class": "A", "confidence": 0.97},
            {"cx": 150.0, "cy": 90.0, "width": 60.0, "height": 90.0, "class": "K", "confidence": 0.96},
        ]

        physical_model = MockDetectorModel("physical")
        # Table ROI and Mine ROI:
        def physical_predict(image, confidence=50, overlap=45):
            h, w = image.shape[:2]
            # Mine crop is smaller (~520x280)
            if w < 700:
                return [
                    {"cx": 100.0, "cy": 120.0, "width": 55.0, "height": 85.0, "class": "10", "confidence": 0.93},
                    {"cx": 170.0, "cy": 120.0, "width": 55.0, "height": 85.0, "class": "8", "confidence": 0.91},
                ]
            else:
                # Table crop (~1620x570)
                return [
                    {"cx": 300.0, "cy": 250.0, "width": 55.0, "height": 85.0, "class": "5", "confidence": 0.89},
                    {"cx": 370.0, "cy": 250.0, "width": 55.0, "height": 85.0, "class": "6", "confidence": 0.88},
                ]
        physical_model.predict = physical_predict

        predictions, meta = predict_layout_dual(
            frame, layout, confidence=50, overlap=45,
            digital_model=digital_model, physical_model=physical_model
        )

        dealer_preds = [p for p in predictions if p["owner"] == "dealer"]
        mine_preds = [p for p in predictions if p["owner"] == "mine"]
        table_preds = [p for p in predictions if p["owner"] == "other"]

        assert len(dealer_preds) == 2
        assert len(mine_preds) == 2
        assert len(table_preds) == 2
        assert len(predictions) == 6

        assert all(p["surface"] == SURFACE_DIGITAL for p in dealer_preds)
        assert all(p["surface"] == SURFACE_PHYSICAL for p in mine_preds)
        assert all(p["surface"] == SURFACE_PHYSICAL for p in table_preds)


# ===========================================================================
# 3. CardState("locked") & discarded_regions Integration (Requirement 3)
# ===========================================================================

class TestLockedPersistenceAndDiscardedRegionsIntegration:
    """Verifies full integration with CardState("locked") and discarded_regions:
    - Digital and physical cards reach locked/confirmed status via temporal consensus, add to RC once.
    - Once locked, cards NEVER disappear nor mutate across 30+ frames even if undetected.
    - If user manual correction is applied or discarded ("not_a_card"), discarded_regions prevents reviving.
    - Click hit-testing on RoundOverlay and TableView works seamlessly for both digital and physical card bboxes.
    """

    def test_digital_and_physical_cards_lock_via_temporal_consensus_and_count_once(self):
        """Digital (dealer) and physical (player) cards transition from pending to locked
        and contribute to Running Count exactly once.
        """
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)

        # Digital dealer card ("10", Hi-Lo: -1) and Physical player card ("5", Hi-Lo: +1)
        d_pred = {
            "cx": 850.0, "cy": 100.0, "width": 60.0, "height": 90.0,
            "name": "10", "class": "10", "owner": "dealer", "confidence": 0.95
        }
        p_pred = {
            "cx": 800.0, "cy": 750.0, "width": 55.0, "height": 85.0,
            "name": "5", "class": "5", "owner": "mine", "confidence": 0.94
        }

        # Frame 1: pending
        memory.observe([d_pred, p_pred], now=1.0)
        assert counter.running_count == 0
        for card in memory.cards.values():
            assert card.state == "pending"
            assert not card.counted

        # Frame 2: temporal consensus -> locked
        memory.observe([d_pred, p_pred], now=1.40)
        # Running count: 10 is -1, 5 is +1 -> total net delta = 0
        assert counter.running_count == 0
        assert len(memory.cards) == 2
        for card in memory.cards.values():
            assert card.state == CardState("locked")
            assert card.is_locked
            assert card.counted

        # Frames 3 to 6: duplicate observations do NOT re-count
        for t in (1.5, 1.6, 1.7, 1.8):
            memory.observe([d_pred, p_pred], now=t)
            assert counter.running_count == 0

    def test_hard_latch_anti_disappearance_across_30_occluded_frames(self):
        """Once locked, cards remain in CardMemory, Presentation, and Overlays
        even when undetected for 30+ consecutive frames (e.g. dealer arm occlusion).
        """
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)

        d_pred = {
            "cx": 850.0, "cy": 100.0, "width": 60.0, "height": 90.0,
            "name": "K", "class": "K", "owner": "dealer", "confidence": 0.95
        }
        p_pred = {
            "cx": 800.0, "cy": 750.0, "width": 55.0, "height": 85.0,
            "name": "8", "class": "8", "owner": "mine", "confidence": 0.94
        }

        # Lock cards over 2 frames
        memory.observe([d_pred, p_pred], now=1.0)
        memory.observe([d_pred, p_pred], now=1.4)
        for card in memory.cards.values():
            assert card.is_locked

        # Simulate 35 consecutive occluded frames with zero detections
        for frame_idx in range(35):
            memory.observe([], now=2.0 + frame_idx * 0.1)

        # Cards must still be in memory and locked
        assert len(memory.cards) == 2
        for card in memory.cards.values():
            assert card.is_locked
            assert card.status == "active"
            assert card.state == CardState("locked")

        # TablePresentation retains both cards
        from dataclasses import asdict
        d_card = asdict(next(c for c in memory.cards.values() if c.owner == "dealer"))
        p_card = asdict(next(c for c in memory.cards.values() if c.owner == "mine"))
        snap = {
            "source_mode": "individual",
            "running": True,
            "round": 1,
            "shoe_id": counter.shoe_id,
            "dealer_cards": [d_card],
            "card_groups": [{"seat_id": "hero", "is_hero": True, "cards": [p_card]}],
        }
        presentation = build_table_presentation(snap, selected_mode="individual", running=True)
        dealer_cards = presentation["dealer"]["cards"]
        assert len(dealer_cards) == 1
        assert dealer_cards[0]["rank"] == "K"
        assert dealer_cards[0].get("state") == "locked"

        hero_cards = presentation["players"][0]["cards"]
        assert len(hero_cards) == 1
        assert hero_cards[0]["rank"] == "8"
        assert hero_cards[0].get("state") == "locked"

    def test_anti_mutation_locked_rank_protection_across_noisy_frames(self):
        """Locked cards never mutate their rank due to subsequent noisy detector reads."""
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)

        d_pred = {
            "cx": 850.0, "cy": 100.0, "width": 60.0, "height": 90.0,
            "name": "A", "class": "A", "owner": "dealer", "confidence": 0.98
        }
        memory.observe([d_pred], now=1.0)
        memory.observe([d_pred], now=1.4)
        d_card = list(memory.cards.values())[0]
        assert d_card.rank == "A"
        assert d_card.is_locked

        # Subsequent frames falsely report rank "4" and "2" at the same location
        noisy_pred_1 = deepcopy(d_pred) | {"name": "4", "class": "4", "confidence": 0.70}
        memory.observe([noisy_pred_1], now=1.5)
        assert d_card.rank == "A"

        noisy_pred_2 = deepcopy(d_pred) | {"name": "2", "class": "2", "confidence": 0.85}
        memory.observe([noisy_pred_2], now=1.6)
        assert d_card.rank == "A"

    def test_manual_correction_not_a_card_registers_discarded_region_and_prevents_reviving(self):
        """Discarding a false detection ('not_a_card') records its bbox in discarded_regions
        and strictly prevents subsequent frames from reviving it.
        """
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)

        # Digital card detected in dealer zone
        d_pred = {
            "cx": 850.0, "cy": 100.0, "width": 60.0, "height": 90.0,
            "name": "10", "class": "10", "owner": "dealer", "confidence": 0.95
        }
        memory.observe([d_pred], now=1.0)
        memory.observe([d_pred], now=1.4)
        card_id = list(memory.cards.keys())[0]
        assert counter.running_count == -1

        # User manual correction: discard as false detection
        res = memory.apply_correction(card_id, expected_round_id=1, action="not_a_card")
        assert res.ok
        assert counter.running_count == 0  # Reverted count
        assert memory.cards[card_id].status == "not_a_card"
        assert len(memory.discarded_regions) == 1
        rx, ry, rw, rh = memory.discarded_regions[0]
        assert (rx, ry, rw, rh) == (850.0, 100.0, 60.0, 90.0)

        # Subsequent frames continue returning predictions at the discarded position
        for t in (1.5, 1.6, 1.7, 1.8):
            memory.observe([d_pred], now=t)

        # The card remains not_a_card and no new card is created at that location
        active_cards = [c for c in memory.cards.values() if c.status == "active"]
        assert len(active_cards) == 0
        assert counter.running_count == 0

    def test_manual_correction_change_rank_updates_count_and_sets_user_locked(self):
        """User manual correction to change rank immediately updates Hi-Lo count
        and sets user_locked = True.
        """
        counter = CardCounter()
        memory = CardMemory(counter, confirmations=2)

        p_pred = {
            "cx": 800.0, "cy": 750.0, "width": 55.0, "height": 85.0,
            "name": "6", "class": "6", "owner": "mine", "confidence": 0.95
        }
        memory.observe([p_pred], now=1.0)
        memory.observe([p_pred], now=1.4)
        card_id = list(memory.cards.keys())[0]
        # 6 is low -> Hi-Lo count +1
        assert counter.running_count == 1

        # Correct rank to "King" (Hi-Lo -1)
        res = memory.apply_correction(card_id, expected_round_id=1, action="set_rank", new_rank="King")
        assert res.ok
        assert counter.running_count == -1
        assert memory.cards[card_id].rank in ("K", "King")
        assert memory.cards[card_id].user_locked

    def test_click_hit_testing_round_overlay_for_digital_and_physical_cards(self):
        """Click hit-testing on RoundOverlay selects the correct card identity
        for both digital dealer cards and physical player cards.
        """
        selected_cards = []
        overlay = make_fake_overlay(on_correct_card=lambda ident: selected_cards.append(ident))

        card_d = {
            "card_id": "shoe:1:1", "rank": "K", "suit": None, "state": "confirmed",
            "owner": "dealer", "round_id": 1, "shoe_id": "shoe",
        }
        card_p = {
            "card_id": "shoe:1:2", "rank": "8", "suit": None, "state": "confirmed",
            "owner": "mine", "round_id": 1, "shoe_id": "shoe",
        }
        presentation = {
            "mode": "individual", "status": "ready", "round_id": 1, "shoe_id": "shoe",
            "dealer": {"cards": [card_d]},
            "players": [{"id": "hero", "label": "Yo", "is_hero": True, "cards": [card_p]}],
        }
        overlay.update_from_presentation(presentation, running=True)
        assert len(overlay._drawn_cards) == 2

        # 1. Click on digital dealer card
        drawn_dealer = next(d for d in overlay._drawn_cards if d["card"]["card_id"] == "shoe:1:1")
        target_identity_d = drawn_dealer["identity"]
        press_d = SimpleNamespace(x=100, y=50, x_root=500, y_root=300)
        release_d = SimpleNamespace(x_root=502, y_root=300)

        overlay._remember_pressed_identity(target_identity_d)
        overlay._pointer_press(press_d)
        overlay._pressed_identity = target_identity_d
        overlay.canvas.find_withtag = lambda tag: [drawn_dealer["item"]] if tag == "current" else []
        overlay.canvas.gettags = lambda item: [drawn_dealer["tag"]]
        overlay._pointer_release(release_d)

        assert len(selected_cards) == 1
        assert selected_cards[-1]["card_id"] == "shoe:1:1"
        assert selected_cards[-1]["rank"] == "K"

        # 2. Click on physical player card
        drawn_player = next(d for d in overlay._drawn_cards if d["card"]["card_id"] == "shoe:1:2")
        target_identity_p = drawn_player["identity"]
        press_p = SimpleNamespace(x=100, y=180, x_root=500, y_root=400)
        release_p = SimpleNamespace(x_root=501, y_root=400)

        overlay._remember_pressed_identity(target_identity_p)
        overlay._pointer_press(press_p)
        overlay._pressed_identity = target_identity_p
        overlay.canvas.find_withtag = lambda tag: [drawn_player["item"]] if tag == "current" else []
        overlay.canvas.gettags = lambda item: [drawn_player["tag"]]
        overlay._pointer_release(release_p)

        assert len(selected_cards) == 2
        assert selected_cards[-1]["card_id"] == "shoe:1:2"
        assert selected_cards[-1]["rank"] == "8"

        # 3. Drag movement (>5px) does not trigger card selection
        release_drag = SimpleNamespace(x_root=520, y_root=400)
        overlay._pointer_press(press_p)
        overlay._pointer_release(release_drag)
        assert len(selected_cards) == 2

    def test_click_hit_testing_table_view_for_digital_and_physical_cards(self, monkeypatch):
        """TableView displays and tracks click selection for both digital and physical cards."""
        for name in ("Canvas", "Label", "Frame", "Button"):
            monkeypatch.setattr(table_module.tk, name, FakeTableViewWidget)
        monkeypatch.setattr(table_module.scaling, "on_change", Mock())
        monkeypatch.setattr(table_module.scaling, "size", lambda size: tuple(size))
        monkeypatch.setattr(table_module.scaling, "px", lambda val: val)
        monkeypatch.setattr(table_module.Image, "open", Mock())
        monkeypatch.setattr(table_module.ImageTk, "PhotoImage", Mock(return_value=object()))

        selected = []
        table_view = TableView(None, on_card_identity_click=lambda c: selected.append(c))

        card_d = {
            "card_id": "shoe:1:1", "rank": "Q", "suit": "Hearts", "state": "confirmed",
            "owner": "dealer", "round_id": 1, "shoe_id": "shoe",
        }
        card_p = {
            "card_id": "shoe:1:2", "rank": "7", "suit": "Spades", "state": "confirmed",
            "owner": "mine", "round_id": 1, "shoe_id": "shoe",
        }
        presentation = {
            "mode": "multiplayer", "status": "ready", "round_id": 1, "shoe_id": "shoe",
            "dealer": {"cards": [card_d]},
            "players": [{"id": "hero", "seat_id": "J1", "label": "J1 (Vos)", "is_hero": True, "cards": [card_p]}],
        }
        table_view.update(presentation)

        # Dispatch click on digital dealer card
        dealer_widget = table_view.dealer["cards"][0]
        table_view._dispatch_card_identity(dealer_widget)
        assert len(selected) == 1
        assert selected[-1]["card_id"] == "shoe:1:1"

        # Dispatch click on physical player card
        player_widget = table_view.seats[0]["cards"][0]
        table_view._dispatch_card_identity(player_widget)
        assert len(selected) == 2
        assert selected[-1]["card_id"] == "shoe:1:2"


# ===========================================================================
# 4. Synthetic Training Data Generation (Requirement 4)
# ===========================================================================

class TestSyntheticTrainingDataGeneration:
    """Verifies synthetic digital card strip generation in tools/prepare_training.py:
    - Sprite generation for all 13 ranks
    - 1 to 5 cards strip layout with spacing or overlap
    - YOLO label formatting
    - Batch generation and filesystem export
    """

    def test_generate_digital_card_sprite_valid_and_invalid_ranks(self):
        """Sprite generator creates clean images for valid ranks and rejects invalid ranks."""
        for r in RANKS:
            sprite = generate_digital_card_sprite(r, width=50, height=75)
            assert sprite.shape == (75, 50, 3)
            assert sprite.dtype == np.uint8

        with pytest.raises(ValueError):
            generate_digital_card_sprite("invalid_rank")
        with pytest.raises(ValueError):
            generate_digital_card_sprite("0")

    @pytest.mark.parametrize("n_cards", [1, 2, 3, 4, 5])
    def test_generate_digital_card_strip_lengths_1_to_5(self, n_cards):
        """generate_digital_card_strip generates valid images and annotations for 1 to 5 cards."""
        sample_ranks = list(RANKS)[:n_cards]
        strip, annotations = generate_digital_card_strip(
            sample_ranks, card_size=(60, 90), spacing=10, overlap_pixels=0
        )
        assert len(annotations) == n_cards
        sh, sw = strip.shape[:2]
        assert sh == 90 + 40  # height + 2 * top_margin (20)
        assert sw > 0

        # Monotonic cx
        cxs = [ann["cx"] for ann in annotations]
        assert cxs == sorted(cxs)

        # All boxes strictly inside strip
        for ann in annotations:
            l, t, r, b = ann["box"]
            assert 0 <= l < r <= sw
            assert 0 <= t < b <= sh
            assert ann["class_idx"] >= 0

    def test_generate_digital_card_strip_spacing_vs_overlap(self):
        """Checks non-overlapping vs overlapping horizontal strip geometry."""
        # Non-overlapping
        _, ann_no_overlap = generate_digital_card_strip(
            ["A", "K"], card_size=(60, 90), spacing=10, overlap_pixels=0
        )
        # Gap between cards: left of second card - right of first card == 10
        assert ann_no_overlap[1]["box"][0] - ann_no_overlap[0]["box"][2] == 10

        # Overlapping
        _, ann_overlap = generate_digital_card_strip(
            ["A", "K"], card_size=(60, 90), spacing=0, overlap_pixels=15
        )
        # Right of first card > left of second card by exactly 15 px
        assert ann_overlap[0]["box"][2] - ann_overlap[1]["box"][0] == 15

    def test_format_yolo_labels_normalized_and_accurate(self):
        """format_yolo_labels produces normalized coordinates in [0, 1] range."""
        _, annotations = generate_digital_card_strip(
            ["10", "A"], card_size=(60, 90), spacing=10
        )
        w, h = 200, 130
        lines = format_yolo_labels(annotations, image_width=w, image_height=h)
        assert len(lines) == 2

        for line in lines:
            parts = line.strip().split()
            assert len(parts) == 5
            cls_idx = int(parts[0])
            cx, cy, bw, bh = map(float, parts[1:])
            assert 0 <= cls_idx < len(RANKS)
            assert 0.0 <= cx <= 1.0
            assert 0.0 <= cy <= 1.0
            assert 0.0 < bw <= 1.0
            assert 0.0 < bh <= 1.0

    def test_generate_synthetic_strip_dataset_in_memory_and_disk(self, tmp_path):
        """generate_synthetic_strip_dataset creates batch samples and persists to disk."""
        out_dir = tmp_path / "dataset"
        samples = generate_synthetic_strip_dataset(
            num_samples=4, output_dir=str(out_dir),
            min_cards=2, max_cards=4, allow_overlap=True, seed=123
        )
        assert len(samples) == 4
        for sample in samples:
            assert "image" in sample
            assert "annotations" in sample
            assert "yolo_labels" in sample
            assert 2 <= len(sample["annotations"]) <= 4

        # Verify disk persistence
        img_files = list((out_dir / "images").glob("*.png"))
        lbl_files = list((out_dir / "labels").glob("*.txt"))
        assert len(img_files) == 4
        assert len(lbl_files) == 4
