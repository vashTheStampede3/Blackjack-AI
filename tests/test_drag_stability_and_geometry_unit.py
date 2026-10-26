"""Comprehensive unit tests for window drag stability, freeze during drag,
CardPicker disambiguation, and Configure event filtering (Etapa 7B).

Requirements covered:
1. Sequencia de arrastre sin teletransporte:
   - Simular <ButtonPress-1> -> múltiples <B1-Motion> con diferentes event.x_root, event.y_root
     (e incluso con event.x y event.y variando arbitrariamente simulando pasar sobre widgets hijos) -> <ButtonRelease-1>.
   - Verificar que la posición (x, y) de la ventana Mini Mesa (MiniTableOverlay / RoundOverlay)
     siga exactamente start_win_x + (event.x_root - start_mouse_x) y start_win_y + (event.y_root - start_mouse_y)
     sin saltos bruscos (<= 1px por pixel de movimiento), sin cambiar width/height y sin teletransportarse.
   - Soportar coordenadas negativas y configuraciones multimonitor.
   - Descartar arrastre si la ventana está bloqueada (locked=True) o si el clic se originó en controles.
2. Congelamiento de redibujado y CaptureGuard durante arrastre:
   - Mientras _is_dragging == True o _pointer_down == True, si se llama a update_from_presentation,
     verificar que NO se modifique la geometría de la ventana ni se ejecuten relayouts/borrados destructivos de canvas.
   - En <ButtonRelease-1>, verificar que se aplique el último snapshot/presentation pendiente guardado.
   - Verificar que si CaptureGuard consulta is_dragging (o _is_dragging / _pointer_down), omite el ocultamiento
     y restauración de la ventana para no cortar el ciclo de arrastre de Windows.
3. Prevención de apertura accidental de CardPicker:
   - Si hubo arrastre (movimiento >= 5px en x o y, o distancia euclidiana >= 5px), en <ButtonRelease-1>
     verificar que JAMÁS se llame a la apertura de CardPicker ni al callback on_correct_card.
   - Si no hubo arrastre (movimiento < 5px) sobre una carta, entonces sí debe despachar la identidad de la carta.
4. Filtro de <Configure> en TableView y RoundOverlay:
   - Verificar que eventos <Configure> donde solo cambian x o y pero width y height se mantienen iguales,
     se ignoran por completo sin disparar relayouts innecesarios.
   - Si el tamaño cambia, verificar que se use debouncing antes de relayout.
"""
from copy import deepcopy
import math
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest

from lib.common import constants
from lib.interfaces import table_view as table_module
from lib.interfaces.capture_guard import CaptureGuard
from lib.interfaces.round_overlay import MiniTableOverlay, RoundOverlay, WIDTH, HEIGHT
from lib.interfaces.table_view import TableView
from tests.test_individual_table_view_unit import Widget as FakeTableViewWidget


# ---------------------------------------------------------------------------
# Lightweight Headless Mocks
# ---------------------------------------------------------------------------

class FakeCanvas:
    """Headless Canvas mock recording items, tags, geometry, and timer jobs."""
    def __init__(self, width=WIDTH, height=HEIGHT):
        self._width = width
        self._height = height
        self.items = {}
        self.bindings = {}
        self.tags = {}
        self.serial = 0
        self.draws = 0
        self.delete_calls = 0
        self._jobs = {}
        self._cancelled_jobs = []
        self._job_counter = 0

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def delete(self, tag):
        self.delete_calls += 1
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

    def find_overlapping(self, x1, y1, x2, y2):
        return []

    def gettags(self, item):
        return self.tags.get(item, ())

    def texts(self):
        return [item.get("text") for item in self.items.values() if item["kind"] == "text"]

    def bind(self, sequence, func, add=None):
        self.bindings[sequence] = func

    def after(self, ms, func, *args):
        self._job_counter += 1
        job_id = f"canvas_job_{self._job_counter}"
        self._jobs[job_id] = (ms, func, args)
        return job_id

    def after_cancel(self, job_id):
        if job_id in self._jobs:
            self._cancelled_jobs.append(job_id)
            del self._jobs[job_id]


class FakeWindow:
    """Headless Toplevel mock for overlay and dialog positioning."""
    def __init__(self, x=100, y=200, width=WIDTH, height=HEIGHT):
        self._x = x
        self._y = y
        self._width = width
        self._height = height
        self._exists = True
        self.attributes_dict = {"-topmost": True}
        self.last_geometry = None
        self.geometry_history = []
        self._jobs = {}
        self._cancelled_jobs = []
        self._job_counter = 0
        self.bindings = {}

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
            self.geometry_history.append(geom)
            m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geom)
            if m:
                self._width = int(m.group(1))
                self._height = int(m.group(2))
                self._x = int(m.group(3))
                self._y = int(m.group(4))
            else:
                m_pos = re.match(r"^([+-]\d+)([+-]\d+)$", geom)
                if m_pos:
                    self._x = int(m_pos.group(1))
                    self._y = int(m_pos.group(2))
        return f"{self._width}x{self._height}+{self._x:+d}+{self._y:+d}"

    def attributes(self, key=None, value=None):
        if key is not None and value is not None:
            self.attributes_dict[key] = value
        elif key is not None:
            return self.attributes_dict.get(key)
        return self.attributes_dict

    def lift(self):
        pass

    def focus_force(self):
        pass

    def update_idletasks(self):
        pass

    def deiconify(self):
        pass

    def withdraw(self):
        pass

    def state(self):
        return "normal"

    def bind(self, sequence, func, add=None):
        self.bindings.setdefault(sequence, []).append(func)

    def after(self, ms, func, *args):
        self._job_counter += 1
        job_id = f"win_job_{self._job_counter}"
        self._jobs[job_id] = (ms, func, args)
        return job_id

    def after_cancel(self, job_id):
        if job_id in self._jobs:
            self._cancelled_jobs.append(job_id)
            del self._jobs[job_id]

    def trigger_after_job(self, job_id):
        if job_id in self._jobs:
            _ms, func, args = self._jobs.pop(job_id)
            return func(*args)


def make_fake_overlay(on_correct_card=None, win_x=100, win_y=200):
    """Instantiate a headless MiniTableOverlay without real Tk window dependencies."""
    overlay = object.__new__(MiniTableOverlay)
    overlay.canvas = FakeCanvas()
    overlay.win = FakeWindow(x=win_x, y=win_y, width=WIDTH, height=HEIGHT)
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

    # Drag & geometry state variables
    overlay._pointer_down = False
    overlay._is_dragging = False
    overlay._start_mouse_x = 0
    overlay._start_mouse_y = 0
    overlay._start_win_x = win_x
    overlay._start_win_y = win_y
    overlay._drag_allowed = True
    overlay._drag_origin = None
    overlay._pointer_start = None
    overlay._pressed_identity = None
    overlay._pending_snapshot = None
    overlay._pending_draw = False
    overlay._last_rendered_size = (WIDTH, HEIGHT)
    overlay._configure_job = None

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
# Requirement 1: Sequencia de arrastre sin teletransporte
# ---------------------------------------------------------------------------

class TestDragSequenceNoTeleport:
    """Verifies that window dragging follows mouse deltas continuously without teleportation."""

    def test_continuous_drag_sequence_matches_mouse_deltas_exactly(self):
        """Simulate ButtonPress-1 -> multiple B1-Motion -> ButtonRelease-1.

        Verifies that window (x, y) precisely follows:
        start_win_x + (event.x_root - start_mouse_x) and start_win_y + (event.y_root - start_mouse_y)
        with no jumps (> 1px per 1px mouse step), width/height constant, and no teleportation,
        even as event.x and event.y fluctuate wildly across child widgets.
        """
        start_win_x, start_win_y = 120, 240
        overlay = make_fake_overlay(win_x=start_win_x, win_y=start_win_y)

        # 1. Pointer press inside draggable header (8 <= y <= 52, x < WIDTH - 245)
        start_mouse_x, start_mouse_y = 170, 260
        press_event = SimpleNamespace(x=50, y=20, x_root=start_mouse_x, y_root=start_mouse_y)
        overlay._pointer_press(press_event)

        assert overlay._pointer_down is True
        assert overlay._is_dragging is False
        assert overlay.is_dragging is True  # True because _pointer_down is True
        assert overlay._start_mouse_x == start_mouse_x
        assert overlay._start_mouse_y == start_mouse_y
        assert overlay._start_win_x == start_win_x
        assert overlay._start_win_y == start_win_y

        # 2. Sequence of B1-Motion steps
        # Initially 4 micro-steps under 5px threshold
        for step in range(1, 5):
            motion_event = SimpleNamespace(
                x_root=start_mouse_x + step,
                y_root=start_mouse_y + step,
                x=step * 10, y=step * 5,  # arbitrary child widget coordinates
            )
            overlay._drag_move(motion_event)
            # Under 5px threshold, window does not move yet
            assert overlay._is_dragging is False
            assert overlay.win.winfo_x() == start_win_x
            assert overlay.win.winfo_y() == start_win_y

        # Now cross 5px threshold and perform 25 gradual steps of 1px increments
        prev_win_x = start_win_x
        prev_win_y = start_win_y
        current_mouse_x = start_mouse_x + 5
        current_mouse_y = start_mouse_y + 5

        for i in range(25):
            current_mouse_x += 1
            current_mouse_y += 1
            # Simulate erratic internal x, y (e.g. crossing labels, cards, canvas boundaries)
            wild_child_x = (i * 37 - 150) % 800
            wild_child_y = (i * 53 - 80) % 300
            motion_event = SimpleNamespace(
                x_root=current_mouse_x,
                y_root=current_mouse_y,
                x=wild_child_x,
                y=wild_child_y,
            )
            overlay._drag_move(motion_event)

            expected_win_x = start_win_x + (current_mouse_x - start_mouse_x)
            expected_win_y = start_win_y + (current_mouse_y - start_mouse_y)

            # Assert exact tracking
            assert overlay._is_dragging is True
            assert overlay.is_dragging is True
            assert overlay.win.winfo_x() == expected_win_x
            assert overlay.win.winfo_y() == expected_win_y

            # Assert geometry format and fixed dimensions
            assert overlay.win.winfo_width() == WIDTH
            assert overlay.win.winfo_height() == HEIGHT
            assert overlay.win.last_geometry == f"{WIDTH}x{HEIGHT}{expected_win_x:+d}{expected_win_y:+d}"

            # Assert smooth continuous movement: step jump <= 1px
            if i > 0:
                jump_x = abs(overlay.win.winfo_x() - prev_win_x)
                jump_y = abs(overlay.win.winfo_y() - prev_win_y)
                assert jump_x <= 1, f"Horizontal teleportation detected: jump={jump_x}px"
                assert jump_y <= 1, f"Vertical teleportation detected: jump={jump_y}px"

            prev_win_x = overlay.win.winfo_x()
            prev_win_y = overlay.win.winfo_y()

        # 3. Pointer release
        release_event = SimpleNamespace(
            x_root=current_mouse_x,
            y_root=current_mouse_y,
            x=wild_child_x,
            y=wild_child_y,
        )
        overlay._pointer_release(release_event)

        assert overlay._pointer_down is False
        assert overlay._is_dragging is False
        assert overlay.is_dragging is False
        # Final position persisted in constants.UI
        assert constants.UI["round_x"] == expected_win_x
        assert constants.UI["round_y"] == expected_win_y

    def test_drag_subthreshold_jitter_under_5px_does_not_move_window(self):
        """Micro-jitter (< 5px in both axes) must not trigger window dragging."""
        overlay = make_fake_overlay(win_x=300, win_y=400)
        start_x, start_y = 350, 420
        overlay._pointer_press(SimpleNamespace(x=50, y=20, x_root=start_x, y_root=start_y))

        # Jitters within radius 5px
        jitters = [(start_x + 2, start_y + 1),
                   (start_x + 3, start_y + 3),
                   (start_x + 4, start_y - 2),
                   (start_x - 3, start_y + 2)]
        for jx, jy in jitters:
            overlay._drag_move(SimpleNamespace(x_root=jx, y_root=jy, x=10, y=10))
            assert overlay._is_dragging is False
            assert overlay.win.winfo_x() == 300
            assert overlay.win.winfo_y() == 400

        # Movement of 5px horizontally activates drag
        overlay._drag_move(SimpleNamespace(x_root=start_x + 5, y_root=start_y, x=10, y=10))
        assert overlay._is_dragging is True
        assert overlay.win.winfo_x() == 305
        assert overlay.win.winfo_y() == 400

    def test_drag_locked_overlay_never_moves_window(self):
        """When overlay is locked, drag motions must be strictly ignored."""
        overlay = make_fake_overlay(win_x=100, win_y=200)
        overlay.locked = True

        overlay._pointer_press(SimpleNamespace(x=50, y=20, x_root=150, y_root=220))
        overlay._drag_move(SimpleNamespace(x_root=350, y_root=450, x=50, y=20))

        assert overlay._is_dragging is False
        assert overlay.win.winfo_x() == 100
        assert overlay.win.winfo_y() == 200

    def test_drag_initiated_on_control_buttons_is_disallowed(self):
        """Pressing control buttons (close, lock, opacity) must prevent window drag."""
        overlay = make_fake_overlay(win_x=100, win_y=200)

        for ctrl_tag in ("close", "lock-control", "opacity-control"):
            item_id = overlay.canvas.create_rectangle(900, 10, 950, 30, tags=(ctrl_tag,))
            overlay.canvas.items[item_id]["tags"] = (ctrl_tag,)
            overlay.canvas.tags[item_id] = (ctrl_tag,)
            overlay.canvas.serial = item_id

            press_event = SimpleNamespace(x=920, y=20, x_root=1020, y_root=220)
            overlay._control_press(press_event)
            overlay._pointer_press(press_event)

            assert overlay._drag_allowed is False or overlay._drag_origin is None

            # Attempt motion
            overlay._drag_move(SimpleNamespace(x_root=1100, y_root=300, x=920, y=20))
            assert overlay._is_dragging is False
            assert overlay.win.winfo_x() == 100
            assert overlay.win.winfo_y() == 200

            overlay._pointer_release(SimpleNamespace(x_root=1100, y_root=300, x=920, y=20))

    def test_drag_supports_negative_and_multi_monitor_coordinates(self):
        """Negative coordinates (e.g. secondary monitor on left) must drag smoothly."""
        overlay = make_fake_overlay(win_x=-1200, win_y=150)

        overlay._pointer_press(SimpleNamespace(x=50, y=20, x_root=-1150, y_root=170))
        overlay._drag_move(SimpleNamespace(x_root=-1050, y_root=250, x=50, y=20))

        # dx = +100, dy = +80 -> win_x = -1100, win_y = 230
        assert overlay.win.winfo_x() == -1100
        assert overlay.win.winfo_y() == 230
        assert overlay.win.last_geometry == f"{WIDTH}x{HEIGHT}-1100+230"

    def test_round_overlay_is_alias_of_mini_table_overlay(self):
        """RoundOverlay and MiniTableOverlay are the same class and behave identically."""
        assert RoundOverlay is MiniTableOverlay
        overlay = object.__new__(RoundOverlay)
        overlay.win = FakeWindow(x=50, y=50)
        overlay._pointer_down = False
        overlay._is_dragging = False
        assert overlay.is_dragging is False


# ---------------------------------------------------------------------------
# Requirement 2: Congelamiento de redibujado y CaptureGuard durante arrastre
# ---------------------------------------------------------------------------

class TestDragRedrawFreezeAndCaptureGuard:
    """Verifies that rendering is frozen during drag and CaptureGuard avoids window toggling."""

    def test_presentation_update_frozen_during_pointer_down(self):
        """While pointer is down, presentation updates must not trigger redraw or geometry change."""
        overlay = make_fake_overlay(win_x=200, win_y=300)
        initial_state = {"status": "ready", "round_id": 1, "dealer": {}, "players": []}
        overlay.update_from_presentation(initial_state, running=True, metrics={"running": 1})
        initial_delete_calls = overlay.canvas.delete_calls

        # Pointer down (pre-drag)
        overlay._pointer_press(SimpleNamespace(x=50, y=20, x_root=250, y_root=320))
        assert overlay.is_dragging is True

        # Incoming engine/detection presentation
        updated_state = {"status": "ready", "round_id": 1, "dealer": {}, "players": []}
        overlay.update_from_presentation(updated_state, running=True, metrics={"running": 3})

        # Canvas must NOT have been destroyed/redrawn
        assert overlay.canvas.delete_calls == initial_delete_calls
        # Update is safely held in pending buffer
        assert overlay._pending_snapshot is not None
        pending_snap, pending_running, pending_metrics = overlay._pending_snapshot
        assert pending_metrics["running"] == 3
        assert overlay._pending_draw is True

    def test_presentation_update_frozen_during_active_drag(self):
        """While actively dragging (_is_dragging=True), successive updates are queued without relayout."""
        overlay = make_fake_overlay(win_x=100, win_y=100)
        overlay._pointer_press(SimpleNamespace(x=50, y=20, x_root=150, y_root=120))
        overlay._drag_move(SimpleNamespace(x_root=170, y_root=140, x=50, y=20))
        assert overlay._is_dragging is True

        initial_geom = overlay.win.last_geometry
        initial_delete_calls = overlay.canvas.delete_calls

        # Multiple stream updates arrive mid-drag
        for r_count in (4, 5, 6):
            state = {"status": "ready", "round_id": 2, "dealer": {}, "players": []}
            overlay.update_from_presentation(state, running=True, metrics={"running": r_count})

        # Window geometry not disturbed by presentation updates (remains at drag location)
        assert overlay.win.last_geometry == initial_geom
        # Canvas not deleted/redrawn
        assert overlay.canvas.delete_calls == initial_delete_calls
        # Buffer holds the newest value (6)
        assert overlay._pending_snapshot[2]["running"] == 6

    def test_button_release_applies_latest_pending_snapshot(self):
        """On ButtonRelease-1, the pending presentation must be immediately applied."""
        overlay = make_fake_overlay(win_x=100, win_y=100)
        overlay._pointer_press(SimpleNamespace(x=50, y=20, x_root=150, y_root=120))
        overlay._drag_move(SimpleNamespace(x_root=170, y_root=140, x=50, y=20))

        latest_state = {"status": "ready", "round_id": 5, "dealer": {}, "players": []}
        overlay.update_from_presentation(latest_state, running=True, metrics={"running": 10, "true": 3.0})
        assert overlay._pending_snapshot is not None

        # Release pointer
        overlay._pointer_release(SimpleNamespace(x_root=170, y_root=140, x=50, y=20))

        # Pending snapshot consumed
        assert overlay._pending_snapshot is None
        assert overlay._last_state["round_id"] == 5
        assert overlay._metrics["running"] == 10
        assert overlay._metrics["true"] == 3.0

    def test_capture_guard_skips_overlay_hide_when_is_dragging(self):
        """CaptureGuard must NOT hide or restore overlay while it is being dragged."""
        overlay = make_fake_overlay()
        overlay.set_capture_hidden = Mock()
        overlay._is_dragging = True

        fake_root = FakeWindow()
        guard = CaptureGuard(fake_root, lambda: overlay)

        with guard.capture():
            pass

        # During dragging, set_capture_hidden is never called
        assert overlay.set_capture_hidden.called is False
        guard.close()

    def test_capture_guard_skips_overlay_hide_when_pointer_down(self):
        """CaptureGuard must NOT hide or restore overlay when pointer is down on overlay."""
        overlay = make_fake_overlay()
        overlay.set_capture_hidden = Mock()
        overlay._pointer_down = True
        overlay._is_dragging = False

        fake_root = FakeWindow()
        guard = CaptureGuard(fake_root, lambda: overlay)

        with guard.capture():
            pass

        assert overlay.set_capture_hidden.called is False
        guard.close()

    def test_capture_guard_hides_and_restores_when_idle(self):
        """CaptureGuard performs normal hide and restore when overlay is idle (not dragging)."""
        overlay = make_fake_overlay()
        overlay.affinity_ok = False
        hidden_log = []
        overlay.set_capture_hidden = lambda val: hidden_log.append(val)

        fake_root = FakeWindow()
        guard = CaptureGuard(fake_root, lambda: overlay)

        with guard.capture():
            assert hidden_log == [True]

        assert hidden_log == [True, False]
        guard.close()

    def test_is_dragging_property_reflects_both_flags(self):
        """overlay.is_dragging must be True if either _is_dragging or _pointer_down is True."""
        overlay = make_fake_overlay()

        overlay._is_dragging = False
        overlay._pointer_down = False
        assert overlay.is_dragging is False

        overlay._is_dragging = True
        overlay._pointer_down = False
        assert overlay.is_dragging is True

        overlay._is_dragging = False
        overlay._pointer_down = True
        assert overlay.is_dragging is True

        overlay._is_dragging = True
        overlay._pointer_down = True
        assert overlay.is_dragging is True


# ---------------------------------------------------------------------------
# Requirement 3: Prevención de apertura accidental de CardPicker
# ---------------------------------------------------------------------------

class TestCardPickerDragDisambiguation:
    """Verifies that dragging prevents accidental card clicks, while stationary clicks dispatch."""

    def _setup_overlay_with_card(self):
        card_identity = {
            "card_id": "hero-card-1",
            "rank": "8",
            "suit": "D",
            "state": "confirmed",
            "round_id": 1,
        }
        on_correct_card = Mock()
        overlay = make_fake_overlay(on_correct_card=on_correct_card)
        overlay._last_state = {
            "round_id": 1,
            "status": "ready",
            "dealer": {"cards": []},
            "players": [{"cards": [deepcopy(card_identity)]}],
        }
        # Register card hitbox at (100, 100, 160, 180)
        overlay._drawn_cards = [{
            "tag": "card-hero-card-1",
            "hitbox": (100, 100, 160, 180),
            "identity": deepcopy(card_identity),
        }]
        return overlay, on_correct_card, card_identity

    def test_drag_ge_5px_never_opens_card_picker(self):
        """Movement >= 5px over a card must NEVER dispatch correction or open CardPicker."""
        overlay, on_correct, _card_identity = self._setup_overlay_with_card()

        # Press on the card
        overlay._pointer_press(SimpleNamespace(x=120, y=130, x_root=220, y_root=330))
        # Move mouse horizontally by 6px (>= 5px)
        overlay._drag_move(SimpleNamespace(x_root=226, y_root=330, x=126, y=130))
        # Release
        overlay._pointer_release(SimpleNamespace(x_root=226, y_root=330, x=126, y=130))

        assert on_correct.called is False

    def test_drag_diagonal_ge_5px_never_opens_card_picker(self):
        """Diagonal movement with euclidean distance >= 5px must not dispatch correction."""
        overlay, on_correct, _card_identity = self._setup_overlay_with_card()

        overlay._pointer_press(SimpleNamespace(x=120, y=130, x_root=200, y_root=200))
        # dx=4, dy=4 -> hypot = ~5.65 >= 5
        overlay._drag_move(SimpleNamespace(x_root=204, y_root=204, x=124, y=134))
        overlay._pointer_release(SimpleNamespace(x_root=204, y_root=204, x=124, y=134))

        assert on_correct.called is False

    def test_stationary_click_dispatches_card_identity_normally(self):
        """Exact click with no movement (< 5px) must dispatch the card identity."""
        overlay, on_correct, card_identity = self._setup_overlay_with_card()

        overlay._pointer_press(SimpleNamespace(x=120, y=130, x_root=200, y_root=200))
        overlay._pointer_release(SimpleNamespace(x_root=200, y_root=200, x=120, y=130))

        assert on_correct.call_count == 1
        dispatched = on_correct.call_args[0][0]
        assert dispatched["card_id"] == "hero-card-1"
        assert dispatched["rank"] == "8"
        assert dispatched["suit"] == "D"

    def test_sub_5px_movement_dispatches_card_identity_normally(self):
        """Click with slight tremor (< 5px, e.g. dx=2, dy=1) must still dispatch."""
        overlay, on_correct, card_identity = self._setup_overlay_with_card()

        overlay._pointer_press(SimpleNamespace(x=120, y=130, x_root=200, y_root=200))
        # dx=2, dy=1 -> hypot = 2.23 < 5
        overlay._drag_move(SimpleNamespace(x_root=202, y_root=201, x=122, y=131))
        overlay._pointer_release(SimpleNamespace(x_root=202, y_root=201, x=122, y=131))

        assert on_correct.call_count == 1
        dispatched = on_correct.call_args[0][0]
        assert dispatched["card_id"] == "hero-card-1"

    def test_click_on_empty_space_never_dispatches_card(self):
        """Click on empty canvas space where no card exists must not dispatch."""
        overlay, on_correct, _card_identity = self._setup_overlay_with_card()

        # Click at (400, 200) outside card hitbox
        overlay._pointer_press(SimpleNamespace(x=400, y=200, x_root=500, y_root=400))
        overlay._pointer_release(SimpleNamespace(x_root=500, y_root=400, x=400, y=200))

        assert on_correct.called is False


# ---------------------------------------------------------------------------
# Requirement 4: Filtro de <Configure> en TableView y RoundOverlay
# ---------------------------------------------------------------------------

class TestConfigureFilteringAndDebounce:
    """Verifies that position-only <Configure> events are ignored and size changes are debounced."""

    def test_round_overlay_configure_position_only_change_is_ignored(self):
        """RoundOverlay must ignore Configure events where only x or y changed, keeping width/height."""
        overlay = make_fake_overlay()
        overlay._last_rendered_size = (WIDTH, HEIGHT)

        # Trigger Configure with identical width and height, but moved window
        event = SimpleNamespace(width=WIDTH, height=HEIGHT, x=450, y=320)
        overlay._on_configure(event)

        # No job scheduled
        assert overlay._configure_job is None
        assert overlay.win._job_counter == 0

    def test_round_overlay_configure_size_change_debounces_with_16ms(self):
        """RoundOverlay must debounce resize events by cancelling pending job and rescheduling for 16ms."""
        overlay = make_fake_overlay()
        overlay._last_rendered_size = (WIDTH, HEIGHT)

        # First resize event
        event1 = SimpleNamespace(width=WIDTH + 50, height=HEIGHT + 20, x=100, y=200)
        overlay._on_configure(event1)

        job1 = overlay._configure_job
        assert job1 is not None
        assert overlay.win._jobs[job1][0] == 16  # 16ms delay

        # Second rapid resize event before 16ms elapsed
        event2 = SimpleNamespace(width=WIDTH + 80, height=HEIGHT + 40, x=100, y=200)
        overlay._on_configure(event2)

        job2 = overlay._configure_job
        assert job2 != job1
        # Previous job cancelled
        assert job1 in overlay.win._cancelled_jobs
        assert job2 in overlay.win._jobs

        # Trigger debounced size change callback
        overlay._draw = Mock()
        overlay.win.trigger_after_job(job2)
        assert overlay._draw.called is True

    def test_round_overlay_size_change_does_not_draw_during_drag(self):
        """When _on_size_change executes while dragging is active, redraw must be skipped."""
        overlay = make_fake_overlay()
        overlay._is_dragging = True
        overlay._draw = Mock()

        overlay._on_size_change()
        assert overlay._draw.called is False

    def test_table_view_configure_position_only_change_is_ignored(self, monkeypatch):
        """TableView._on_resize must ignore events where width and height are unchanged."""
        tv = make_fake_table_view(monkeypatch)
        tv._last_rendered_size = (1000, 700)

        # Configure event with same width/height
        event = SimpleNamespace(width=1000, height=700, x=200, y=300)
        tv._on_resize(event)

        assert tv._resize_job is None
        assert len(tv.canvas.jobs) == 0

    def test_table_view_configure_size_change_debounces_relayout(self, monkeypatch):
        """TableView._on_resize must debounce resize events before calling _relayout."""
        tv = make_fake_table_view(monkeypatch)
        tv._last_rendered_size = (1000, 700)

        event1 = SimpleNamespace(width=1100, height=750, x=0, y=0)
        tv._on_resize(event1)

        job1 = tv._resize_job
        assert job1 is not None
        assert job1 in tv.canvas.jobs
        assert tv.canvas.jobs[job1][0] == 16

        # Mock _relayout before resize to verify that the scheduled callback invokes it
        tv._relayout = Mock()
        # Second event immediately after
        event2 = SimpleNamespace(width=1200, height=800, x=0, y=0)
        tv._on_resize(event2)

        job2 = tv._resize_job
        assert job2 != job1
        assert job1 in tv.canvas.cancelled

        # Trigger debounced callback
        _delay, callback = tv.canvas.jobs[job2]
        callback()
        assert tv._relayout.called is True

    def test_table_view_relayout_ignores_collapsed_dimensions(self, monkeypatch):
        """TableView._relayout must safely return early when canvas dimensions are < 50px."""
        tv = make_fake_table_view(monkeypatch)
        tv.canvas.width = 30
        tv.canvas.height = 30

        # Should return safely without raising
        tv._relayout()
        assert tv._resize_job is None
