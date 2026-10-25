"""In-memory checks of overlay safety and offline calibration. No casino input."""
import copy
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from lib.logic.image_source import ImageSource
from lib.logic.round_observation import overlay_text, RoundObservation


def sample(now=100):
    return {"round": 3, "count": {"running": 3, "true": .8},
            "reading": {"captured_at": now, "inferred_at": now, "present": True,
                        "dealer_ok": True, "seats": {3: True, 5: True}},
            "seats": [{"index": 3, "mine": True, "cards": ["10", "9"],
                       "optimal_action": "S", "book_action": "S"},
                      {"index": 5, "mine": True, "split": True, "cards": ["8", "8"],
                       "optimal_action": ["H", "S"], "book_action": ["H", "S"]}]}


def test_overlay_multi_seat_split_and_immutable_input():
    snap = sample()
    previous = copy.deepcopy(snap)
    state, count, action = overlay_text(snap, True, 100)
    assert state == "active" and "RC +3" in count
    assert "P4: Stand" in action and "P6: H1 Hit · H2 Stand" in action
    assert snap == previous


@pytest.mark.parametrize("change,expected", [
    (lambda s: s.update(error="failed"), "Error"),
    (lambda s: s["reading"].update(captured_at=90), "Sin actualización"),
    (lambda s: s["reading"].update(dealer_ok=False), "Lectura incompleta"),
    (lambda s: s["reading"].update(inferred_at=90), "Lectura incompleta"),
    (lambda s: [seat.update(mine=False) for seat in s["seats"]], "Seleccioná"),
])
def test_overlay_withholds_unreliable_actions(change, expected):
    snap = sample()
    change(snap)
    state, count, action = overlay_text(snap, True, 100)
    assert state == "warning" and expected in action
    assert "Stand" not in action and "Hit" not in action


def test_overlay_idle_stopped_and_count_uncertainty():
    snap = sample()
    snap["reading"]["present"] = False
    assert overlay_text(snap, True, 100)[0] == "idle"
    assert "detenida" in overlay_text(sample(), False, 100)[2]
    snap = sample()
    snap["reading"]["count_uncertain"] = True
    snap["seats"][0].update(optimal_action="D", book_action="H")
    state, count, action = overlay_text(snap, True, 100)
    assert "?" in count and "P4: Hit" in action and "Double" not in action


def test_image_mapping_and_crop_do_not_stretch_mismatched_framing():
    frame = np.zeros((600, 1000, 3), np.uint8)
    frame[100:500, 100:900] = (20, 100, 200)
    source = ImageSource(frame)
    source.resolution = (1600, 800)
    with pytest.raises(ValueError, match="framing"):
        source.grab_bgr()
    source.set_crop((100, 100, 900, 500))
    mapped = source.grab_bgr()
    assert mapped.shape == (800, 1600, 3)
    assert np.all(mapped == [20, 100, 200])
    assert source.original.shape == frame.shape


@pytest.mark.parametrize("format", ["PNG", "JPEG", "BMP", "TIFF", "WEBP"])
def test_common_image_formats_decode_locally_in_memory(format):
    from io import BytesIO
    from PIL import Image
    stream = BytesIO()
    Image.new("RGB", (320, 200), (20, 100, 200)).save(stream, format=format)
    decoder = Image.open
    with patch("lib.logic.image_source.Image.open", side_effect=lambda path: decoder(BytesIO(stream.getvalue()))):
        source = ImageSource.open("fixture."+format.lower(), (640, 400))
    assert source.grab_bgr().shape == (400, 640, 3)


def test_preview_uses_same_local_model_and_frame_without_counting():
    from lib.logic.region_preview import build_region_preview
    calls = []
    class Model:
        hosted = False
        def predict(self, frame, *args):
            calls.append(frame.copy())
            return [{"class": "A", "cx": 80, "cy": 90, "width": 25,
                     "height": 40, "confidence": .95}]
    source = ImageSource(np.full((480, 640, 3), 50, np.uint8))
    engine = SimpleNamespace(capture=source, regions=[],
                             provider=SimpleNamespace(players_model=lambda: Model()))
    with patch("lib.logic.region_preview.scaled_player_regions", return_value=[]):
        live = build_region_preview(engine)
        offline = build_region_preview(engine, source)
    assert np.array_equal(calls[0], calls[1])
    assert np.array_equal(np.asarray(live), np.asarray(offline))
    Model.hosted = True
    with pytest.raises(ValueError, match="not uploaded"):
        build_region_preview(engine, source)


@pytest.mark.skipif(os.name != "nt", reason="Windows native overlay")
def test_native_overlay_focus_clickthrough_capture_exclusion_and_minimized_parent():
    import ctypes
    from ctypes import wintypes as W
    import tkinter as tk
    from lib.interfaces.round_overlay import RoundOverlay
    from lib.interfaces.capture_guard import CaptureGuard
    from lib.logic.monitor_utils import ScreenCapture
    from lib.logic.window_capture import WindowSource

    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    root.title("Overlay verification — own fixture")
    root.geometry("700x450+80+80")
    root.configure(bg="#27a365")
    root.attributes("-topmost", True)
    root.update()
    u = ctypes.WinDLL("user32")
    u.GetForegroundWindow.restype = W.HWND
    u.GetFocus.restype = W.HWND
    u.GetAncestor.argtypes, u.GetAncestor.restype = [W.HWND, W.UINT], W.HWND
    u.IsWindowVisible.argtypes = [W.HWND]
    u.WindowFromPoint.argtypes, u.WindowFromPoint.restype = [W.POINT], W.HWND
    root_hwnd = u.GetAncestor(root.winfo_id(), 2)
    root.focus_force()
    root.update()
    previous_focus = u.GetForegroundWindow()
    previous_keyboard = u.GetFocus()
    overlay = None
    guard = None
    try:
        source = SimpleNamespace(x=root.winfo_rootx(), y=root.winfo_rooty(), width=700, height=450)
        capture = ScreenCapture()
        capture.set_monitor(source)
        time.sleep(.25)
        root.update()
        ctypes.windll.dwmapi.DwmFlush()
        baseline = capture.grab_bgr()
        assert np.allclose(baseline[10:-10, 10:-10], [101, 163, 39], atol=1), "Fixture must fully cover capture"
        overlay = RoundOverlay(root)
        overlay.geometry(f"+{source.x+30}+{source.y+40}")
        overlay.update_from_snapshot(sample(time.time()), True)
        root.update()
        time.sleep(.15)
        root.update()
        assert u.IsWindowVisible(overlay.hwnd)
        assert overlay.winfo_width() > 100 and overlay.count_label.winfo_height() > 10
        overlay.move_to(32000, 32000)
        root.update()
        overlay.ensure_on_screen()
        root.update()
        assert overlay.winfo_x() < 32000 and overlay.winfo_y() < 32000
        overlay.move_to(source.x+30, source.y+40)
        root.update()
        assert u.GetForegroundWindow() == previous_focus, "Overlay stole focus"
        assert u.GetFocus() == previous_keyboard, "Overlay took keyboard focus"
        point = W.POINT(source.x+60, source.y+65)
        hit = u.WindowFromPoint(point)
        assert u.GetAncestor(hit, 2) == root_hwnd, "Overlay intercepts mouse hit testing"
        overlay.set_editing(True)
        root.update()
        hit = u.WindowFromPoint(point)
        assert u.GetAncestor(hit, 2) == overlay.hwnd
        overlay._drag_start(SimpleNamespace(x_root=source.x+60, y_root=source.y+65))
        old_position = (overlay.winfo_x(), overlay.winfo_y())
        overlay._drag_move(SimpleNamespace(x_root=source.x+70, y_root=source.y+75))
        root.update()
        assert (overlay.winfo_x(), overlay.winfo_y()) == (old_position[0]+10, old_position[1]+10)
        overlay.geometry(f"+{source.x+30}+{source.y+40}")
        root.update()
        overlay.set_editing(False)
        assert overlay.affinity_ok, "Capture exclusion unsupported on this Windows"
        # This screenshot is confined to an opaque test fixture and stays in RAM.
        ctypes.windll.dwmapi.DwmFlush()
        excluded = capture.grab_bgr()
        assert np.array_equal(baseline, excluded), "Overlay or black hole entered monitor capture"
        target = WindowSource(int(root_hwnd), root.title(), os.getpid(), 700, 450)
        assert np.array_equal(target.grab_bgr()[20:-20, 20:-20], baseline[20:-20, 20:-20])
        guard = CaptureGuard(root, lambda: overlay)
        guard.force_hide = True
        capture.guard = guard
        assert np.array_equal(baseline, capture.grab_bgr()), "Fallback hiding contaminated capture"
        assert u.IsWindowVisible(overlay.hwnd)
        assert u.GetForegroundWindow() == previous_focus
        overlay.set_opacity(.65)
        assert abs(overlay.attributes("-alpha")-.65) < .02
        capture.guard = None
        ctypes.windll.dwmapi.DwmFlush()
        assert np.array_equal(baseline, capture.grab_bgr()), "Changing opacity broke exclusion"
        capture.guard = guard
        # Exercise the worker->UI fallback path; no Tk calls on that worker.
        import threading
        received = []
        worker = threading.Thread(target=lambda: received.append(capture.grab_bgr()))
        worker.start()
        deadline = time.monotonic()+3
        while worker.is_alive() and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        worker.join(timeout=.1)
        assert not worker.is_alive() and len(received) == 1
        assert np.array_equal(baseline, received[0])
        # Fullscreen test is our opaque fixture, never the casino/VM.
        root.attributes("-fullscreen", True)
        root.update()
        root_hwnd = u.GetAncestor(root.winfo_id(), 2)
        overlay._show_noactivate()
        root.update()
        assert u.IsWindowVisible(overlay.hwnd)
        assert u.GetAncestor(u.WindowFromPoint(point), 2) == root_hwnd
        overlay.set_editing(True)
        root.update()
        assert u.GetAncestor(u.WindowFromPoint(point), 2) == overlay.hwnd
        overlay.set_editing(False)
        capture.guard = None
        ctypes.windll.dwmapi.DwmFlush()
        assert np.allclose(capture.grab_bgr()[10:-10,10:-10], [101, 163, 39], atol=1)
        capture.guard = guard
        root.attributes("-fullscreen", False)
        root.update()
        idle = sample(time.time())
        idle["reading"]["present"] = False
        overlay.update_from_snapshot(idle, True)
        assert overlay.attributes("-alpha") <= .31
        root.iconify()
        root.update()
        assert u.IsWindowVisible(overlay.hwnd), "Minimizing main window hid overlay"
        root.deiconify()
        root.update()
    finally:
        if guard:
            guard.close()
        if overlay:
            overlay.close()
        root.destroy()


def test_round_observation_rejects_conflicts_low_scores_and_clears_on_empty():
    state = RoundObservation()
    seat = SimpleNamespace(index=0, cards=[{"name": "10"}, {"name": "Queen"}])
    engine = SimpleNamespace(seats=[seat], _pending_extra={}, _pending_dealer={},
                             dealer_card="Ace", dealer_locked=True, dealer_extras=[],
                             _seat_for_point=lambda x, y: 0 if y > 100 else None)
    dealer = [{"class": "A", "cx": 50, "cy": 50, "confidence": .95}]
    hand = [{"class": "10", "cx": 40, "cy": 150, "confidence": .95},
            {"class": "Q", "cx": 80, "cy": 150, "confidence": .95}]
    state.captured_at = 100
    state.observe(engine, hand, dealer)
    assert state.present and state.seats[0] and state.dealer_ok
    hand[1]["class"] = "9"
    state.observe(engine, hand, dealer)
    assert not state.seats[0] and state.count_uncertain
    hand[1].update({"class": "Q", "confidence": .6})
    state.observe(engine, hand, dealer)
    assert not state.seats[0]
    for _ in range(3):
        state.observe(engine, [], [])
    assert not state.present
    state.reset_round()
    assert state.count_uncertain, "Round reset must not certify a doubtful shoe"


def test_simulated_round_through_existing_engine_without_casino_or_store(monkeypatch):
    from lib.common import constants
    from lib.logic.engine import DetectionEngine
    from lib.logic.monitor_utils import Polygon
    from lib.logic.models import ModelProvider
    monkeypatch.setitem(constants.BETTING, "use_exact_edge", 0)
    monkeypatch.setitem(constants.TRAINING, "enabled", 0)
    monkeypatch.setitem(constants.OCR, "enabled", 0)
    monkeypatch.setitem(constants.PHASE, "enabled", 0)
    monkeypatch.setattr("lib.logic.engine.SessionStore", lambda: None)
    monkeypatch.setattr(DetectionEngine, "_warm_advice_job", lambda *a: None)
    monkeypatch.setattr(DetectionEngine, "_optimal_advice", lambda self, n, d, p, action, **kw: ("", "white", None))
    predictions = [
        {"class": "A", "cx": 50, "cy": 50, "width": 20, "height": 30, "confidence": .95},
        {"class": "10", "cx": 80, "cy": 200, "width": 20, "height": 30, "confidence": .95},
        {"class": "Q", "cx": 180, "cy": 200, "width": 20, "height": 30, "confidence": .95}]
    model = SimpleNamespace(hosted=False, predict=lambda *args: copy.deepcopy(predictions))
    dealer_model = SimpleNamespace(hosted=False, predict=lambda *args: [])
    provider = SimpleNamespace(backend_name="Local fixture", players_model=lambda: model,
                                dealer_model=lambda: dealer_model)
    monkeypatch.setattr(ModelProvider, "get", lambda: provider)
    engine = DetectionEngine(log=lambda *args, **kw: None)
    engine.capture.set_monitor(SimpleNamespace(width=640, height=480))
    monkeypatch.setattr(engine.capture, "grab_bgr", lambda: np.full((480, 640, 3), 50, np.uint8))
    monkeypatch.setattr(engine, "_frame_unchanged", lambda frame: False)
    engine.regions = [Polygon([(0, 120), (300, 120), (300, 400), (0, 400)])] + [
        Polygon([(400, 120), (420, 120), (420, 150)]) for _ in range(6)]
    engine._dealer_rect = (0, 0, 100, 100)
    engine.my_seats = {0}
    try:
        for _ in range(3):
            engine.run_cycle()
        snapshot = engine.get_snapshot()
        assert snapshot["reading"]["dealer_ok"] and snapshot["reading"]["seats"][0]
        assert snapshot["count"]["cards_seen"] == 3
        assert overlay_text(snapshot, True)[0] == "active"
        predictions.clear()
        for _ in range(3):
            engine.run_cycle()
        assert overlay_text(engine.get_snapshot(), True)[0] == "idle"
        assert engine.counter.cards_seen == 3
    finally:
        for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
            getattr(engine, name).shutdown(wait=True, cancel_futures=True)


@pytest.mark.skipif(os.name != "nt", reason="Tk Windows integration")
def test_main_gui_offline_preview_calibration_and_independent_panels(monkeypatch):
    import tkinter as tk
    import traceback
    from lib.common import constants, settings
    from lib.logic.engine import DetectionEngine
    from lib.interfaces.modern_gui import ModernBlackjackGUI
    from lib.interfaces.region_editor import RegionEditor
    from lib.interfaces.ocr_region_editor import OcrRegionEditor
    from lib.logic import monitor_utils, ocr
    from lib.logic.models import ModelProvider

    monkeypatch.setitem(constants.BETTING, "use_exact_edge", 0)
    monkeypatch.setitem(constants.EXECUTOR, "mode", "off")
    monkeypatch.setattr("lib.logic.engine.SessionStore", lambda: None)
    monkeypatch.setattr(settings, "save", lambda *a, **k: None)
    monkeypatch.setattr(DetectionEngine, "_warm_advice_job", lambda *a: None)
    monkeypatch.setattr(ModernBlackjackGUI, "_maybe_restore_shoe", lambda *a: None)
    calls = []
    class Model:
        hosted = False
        def predict(self, frame, *args):
            calls.append(frame.shape)
            return []
    provider = SimpleNamespace(backend_name="Local fixture", players_model=lambda: Model())
    monkeypatch.setattr(ModelProvider, "get", lambda: provider)
    app = ModernBlackjackGUI()
    errors = []
    app.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
    engine = app.controller.engine
    live_capture = engine.capture
    monkeypatch.setattr(live_capture, "grab_bgr", lambda: np.full((1200, 1920, 3), 40, np.uint8))

    def pump(seconds=.1):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            app.update()
            time.sleep(.01)

    def wait_preview():
        deadline = time.monotonic()+4
        while app._preview_busy and time.monotonic() < deadline:
            pump(.05)
        assert not app._preview_busy
        assert app.table._preview_item is not None

    def run():
        try:
            assert app.round_enabled.get(), "Round panel must be enabled at startup"
            assert app.round_overlay is not None and app.round_overlay.winfo_exists()
            before_count = engine.counter.cards_seen
            original_monitor = app.monitor
            source = ImageSource(np.full((480, 640, 3), 80, np.uint8), (2560, 1920))
            app._preview_source = source
            app._preview_regions()
            wait_preview()
            assert (1920, 2560, 3) in calls
            controls = [str(w.cget("text")) for w in app.table._preview_close_btn.winfo_children()]
            assert all(label in controls for label in ("Abrir imagen", "Volver a vivo", "Calibrar regiones", "OCR Regions"))
            app._calibrate_regions()
            editor = next(w for w in app.winfo_children() if isinstance(w, RegionEditor))
            assert editor.resolution == (2560, 1920) and editor.scale < 1
            editor.polys[0] = [[100*editor.scale, 100*editor.scale],
                               [300*editor.scale, 100*editor.scale],
                               [300*editor.scale, 250*editor.scale]]
            with patch.object(monitor_utils, "save_custom_regions") as save:
                editor._save()
            assert save.call_args.args[0] == (2560, 1920)
            assert save.call_args.args[1][0][0] == [100, 100]
            wait_preview()
            app._calibrate_ocr()
            editor = next(w for w in app.winfo_children() if isinstance(w, OcrRegionEditor))
            assert editor._frame_bgr.shape == (1920, 2560, 3) and editor.scale < 1
            assert not editor._allow_remote
            editor.rects["balance"] = [[100*editor.scale, 80*editor.scale],
                                       [400*editor.scale, 130*editor.scale]]
            with patch.object(ocr, "save_regions") as save:
                editor._save()
            assert save.call_args.args[0] == (2560, 1920)
            assert save.call_args.args[1]["balance"] == [100, 80, 400, 130]
            wait_preview()
            app._align_preview_image()
            from lib.interfaces.image_crop import ImageCrop
            crop = next(w for w in app.winfo_children() if isinstance(w, ImageCrop))
            crop._start(SimpleNamespace(x=0, y=0))
            crop._move(SimpleNamespace(x=100, y=100))
            crop._save()
            wait_preview()
            assert source.crop == (0, 0, 100, 75)
            # File selection is stubbed; image bytes stay in RAM.
            with patch("lib.interfaces.modern_gui.filedialog.askopenfilename", return_value="fixture.png"), patch.object(ImageSource, "open", return_value=source):
                app._open_preview_image()
            wait_preview()
            app._preview_live()
            wait_preview()
            assert app._preview_source is None
            assert engine.capture is live_capture and app.monitor is original_monitor
            assert engine.counter.cards_seen == before_count
            app.round_enabled.set(True)
            app._toggle_round_overlay()
            app._toggle_hud()
            pump(.1)
            assert app.round_overlay.winfo_exists() and app.hud.winfo_exists()
            app.round_enabled.set(False)
            app._toggle_round_overlay()
            assert app.round_overlay is None and app.hud.winfo_exists() and app.winfo_exists()
            app._toggle_hud()
            assert app.executor.mode == "off" and not app.executor.armed
            assert not errors
        except Exception:
            errors.append(traceback.format_exc())
        finally:
            app._on_close()
    app.after(800, run)
    app.mainloop()
    assert not errors, "\n".join(errors)
