"""Exercise video calibration with generated frames, without viewing screenshots."""
from types import SimpleNamespace
import tkinter as tk

import numpy as np
import pytest

from lib.common import constants
from lib.interfaces.region_editor import RegionEditor
from lib.interfaces.video_frame_picker import VideoFramePicker
from lib.logic import region_profiles
from lib.logic.video_source import VideoFrameSource
from tests.test_video_source import video


@pytest.fixture(scope="module")
def root():
    app = tk.Tk()
    app.withdraw()
    yield app
    app.destroy()


@pytest.fixture
def ui(root, tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path / "profiles")
    monkeypatch.setattr("requests.post", lambda *a, **k: pytest.fail("Network used"))
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("Network used"))
    yield root
    for window in root.winfo_children():
        if isinstance(window, VideoFramePicker):
            window.close()
        elif window.winfo_exists():
            window.destroy()


def test_choose_draw_save_reload_exact_profile_and_frozen_frame(ui, video):
    picker = VideoFramePicker(ui, video_path=video, profile="My layout")
    ui.update()
    assert picker.seek(.2)
    before = picker.source.grab_bgr()
    picker._edit_regions()
    ui.update()
    editor = picker.editor
    assert not picker.seek(.5)
    assert np.array_equal(editor._frame_bgr, before)
    editor.draw_mode.set(True)
    for seat in range(7):
        editor.zone_combo.current(seat)
        editor._press(SimpleNamespace(x=5 + seat * 40, y=90))
        editor._release(SimpleNamespace(x=35 + seat * 40, y=150))
    editor.zone_combo.current(7)
    editor._press(SimpleNamespace(x=100, y=20))
    editor._release(SimpleNamespace(x=190, y=60))
    editor._save()
    ui.update()
    saved = region_profiles.get_regions((320, 180), profile="My layout")
    assert len(saved["players"]) == 7
    assert saved["players"][0] == [[5., 90.], [35., 90.], [35., 150.], [5., 150.], [5., 90.]]
    assert saved["dealer"] == [100, 20, 190, 60]
    assert saved["reference_source"]["frame_index"] == 2
    assert saved["reference_source"]["resolution"] == [320, 180]
    assert len(picker.canvas.find_withtag("regions")) == 15
    assert picker.source.frame_index == 2
    assert picker.seek(.5) and picker.source.frame_index == 5
    assert sorted(p.name for p in constants.OUTPUT_DIR.iterdir()) == ["region_profiles.json"]


def test_cancel_and_bad_time_do_not_write_or_replace_frame(ui, video):
    picker = VideoFramePicker(ui, video_path=video, profile="Unsaved")
    ui.update()
    assert picker.seek(.3)
    picker.time_var.set("9:99")
    assert not picker._go()
    assert picker.source.frame_index == 3
    picker._edit_regions()
    ui.update()
    picker.editor.destroy()
    assert not region_profiles.store_path().exists()
    assert picker.seek(.4)
    reader = picker.reader
    picker.close()
    assert reader._capture is None


def test_failed_save_keeps_editor_open_and_does_not_announce_success(ui, video, monkeypatch):
    picker = VideoFramePicker(ui, video_path=video, profile="Locked")
    picker._edit_regions()
    ui.update()
    errors, saved = [], []
    monkeypatch.setattr("lib.interfaces.region_editor.monitor_utils.save_custom_regions", lambda *a, **k: None)
    monkeypatch.setattr("lib.interfaces.region_editor.messagebox.showerror", lambda *a, **k: errors.append(a))
    picker.editor.on_save = lambda *a: saved.append(True)
    picker.editor._save()
    assert errors and not saved and picker.editor.winfo_exists()
    assert not region_profiles.store_path().exists()


def test_scaled_editor_saves_native_coordinates_and_preserves_other_profile(ui):
    sentinel = {"players": [[[1, 1], [2, 1], [2, 2]]] * 7, "dealer": [1, 1, 2, 2]}
    region_profiles.set_regions((800, 450), sentinel, profile="Keep me")
    source = VideoFrameSource(np.zeros((900, 1600, 3), np.uint8), "sample.avi", 12, .4)
    editor = RegionEditor(ui, source, profile="Large video")
    ui.update()
    assert editor.scale < 1
    editor.draw_mode.set(True)
    editor.zone_combo.current(0)
    editor._press(SimpleNamespace(x=round(100*editor.scale), y=round(200*editor.scale)))
    editor._release(SimpleNamespace(x=round(300*editor.scale), y=round(400*editor.scale)))
    editor._save()
    data = region_profiles.get_regions((1600, 900), profile="Large video")
    points = np.array(data["players"][0])
    assert np.allclose(points.min(axis=0), [100, 200], atol=2)
    assert np.allclose(points.max(axis=0), [300, 400], atol=2)
    assert region_profiles.get_regions((800, 450), profile="Keep me") == sentinel
    assert region_profiles.get_regions((1280, 720), profile="Large video") is None
