"""Exercise video calibration with generated frames, without viewing screenshots."""
from types import SimpleNamespace
import tkinter as tk

import numpy as np
import pytest

from lib.common import constants
from lib.interfaces.region_editor import RegionEditor
from lib.interfaces.video_frame_picker import VideoFramePicker
from lib.logic import region_profiles
from lib.logic.card_layout import SEVEN_SEATS, SINGLE_SEAT, TABLE, MODE_LABELS
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


def draw_zone(editor, index, left, top, right, bottom):
    editor.draw_mode.set(True)
    editor.zone_combo.current(index)
    editor._press(SimpleNamespace(x=left * editor.scale, y=top * editor.scale))
    editor._release(SimpleNamespace(x=right * editor.scale, y=bottom * editor.scale))


@pytest.mark.parametrize("mode", [TABLE, SINGLE_SEAT])
def test_new_modes_draw_save_reload_without_replacing_seven_seats(ui, video, mode):
    old = {"players": [[[1, 1], [2, 1], [2, 2]]] * 7, "dealer": [1, 1, 2, 2]}
    region_profiles.set_regions((320, 180), old, profile="Both modes")
    picker = VideoFramePicker(ui, video_path=video, profile="Both modes")
    picker.mode_var.set(MODE_LABELS[mode])
    picker._mode_changed()
    assert picker.seek(.2)
    picker._edit_regions()
    ui.update()
    editor = picker.editor
    assert list(editor.zone_combo["values"]) == ["Mesa", "Mi asiento", "Crupier"]
    draw_zone(editor, 0, 5, 5, 315, 175)
    draw_zone(editor, 1, 20, 100, 100, 160)
    draw_zone(editor, 2, 100, 20, 200, 60)
    assert not picker.seek(.4)
    editor._save()
    ui.update()
    saved = region_profiles.get_card_layout((320, 180), mode, profile="Both modes")
    assert saved.owner_at(50, 120) == "mine"
    assert saved.reference_source["frame_index"] == 2
    assert len(picker.canvas.find_withtag("regions")) == 6
    assert region_profiles.get_regions((320, 180), profile="Both modes") == old
    picker.close()
    picker = VideoFramePicker(ui, video_path=video, profile="Both modes")
    assert picker.mode_var.get() == MODE_LABELS[mode]
    picker.mode_var.set(MODE_LABELS[SEVEN_SEATS])
    picker._mode_changed()
    assert len(picker.canvas.find_withtag("regions")) == 15


def test_missing_invalid_zones_and_cancel_do_not_save(ui, video, monkeypatch):
    picker = VideoFramePicker(ui, video_path=video, profile="No defaults")
    picker.mode_var.set(MODE_LABELS[TABLE])
    picker._mode_changed()
    picker._edit_regions()
    editor = picker.editor
    errors = []
    monkeypatch.setattr("lib.interfaces.region_editor.messagebox.showerror", lambda *a, **k: errors.append(a))
    editor._save()
    assert errors and editor.winfo_exists() and not region_profiles.store_path().exists()
    draw_zone(editor, 0, 5, 5, 315, 175)
    draw_zone(editor, 1, 20, 100, 100, 160)
    draw_zone(editor, 2, 50, 120, 150, 170)
    editor._save()
    assert len(errors) == 2 and editor.winfo_exists() and not region_profiles.store_path().exists()
    editor.destroy()
    picker._edit_regions()
    assert not region_profiles.store_path().exists()


def test_new_mode_adds_polygon_vertex_and_failed_save_stays_open(ui, video, monkeypatch):
    picker = VideoFramePicker(ui, video_path=video, profile="Polygon")
    picker.mode_var.set(MODE_LABELS[TABLE])
    picker._mode_changed()
    picker._edit_regions()
    editor = picker.editor
    draw_zone(editor, 0, 5, 5, 315, 175)
    draw_zone(editor, 1, 20, 100, 100, 160)
    draw_zone(editor, 2, 100, 20, 200, 60)
    editor.zone_combo.current(0)
    editor._edit_vertex(SimpleNamespace(x=160 * editor.scale, y=5 * editor.scale))
    errors = []
    monkeypatch.setattr("lib.interfaces.region_editor.messagebox.showerror", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(region_profiles, "set_card_layout", lambda *a, **k: None)
    editor._save()
    assert errors and editor.winfo_exists() and not region_profiles.store_path().exists()


def test_polygon_vertices_survive_save_and_scaled_native_reload(ui):
    source = VideoFrameSource(np.zeros((900, 1600, 3), np.uint8), "large.avi", 0, 0.)
    editor = RegionEditor(ui, source, profile="Polygon", mode=TABLE)
    assert editor.scale < 1
    draw_zone(editor, 0, 10, 10, 1590, 890)
    draw_zone(editor, 1, 100, 500, 500, 800)
    draw_zone(editor, 2, 600, 100, 1000, 300)
    editor.zone_combo.current(0)
    editor._edit_vertex(SimpleNamespace(x=800 * editor.scale, y=10 * editor.scale))
    editor._save()
    loaded = region_profiles.get_card_layout((1600, 900), TABLE, profile="Polygon")
    assert len(loaded.table) == 5
    assert np.allclose(loaded.table[1], [800, 10], atol=.2)
    assert loaded.owner_at(300, 700) == "mine"
    assert loaded.owner_at(800, 200) == "dealer"
    assert region_profiles.get_card_layout((1280, 720), TABLE, profile="Polygon") is None


def test_existing_valid_layout_survives_rejected_edit(ui, video, monkeypatch):
    picker = VideoFramePicker(ui, video_path=video, profile="Keep saved")
    picker.mode_var.set(MODE_LABELS[TABLE])
    picker._mode_changed()
    picker._edit_regions()
    editor = picker.editor
    draw_zone(editor, 0, 5, 5, 315, 175)
    draw_zone(editor, 1, 20, 100, 100, 160)
    draw_zone(editor, 2, 100, 20, 200, 60)
    editor._save()
    before = region_profiles.store_path().read_bytes()
    picker._edit_regions()
    editor = picker.editor
    errors = []
    monkeypatch.setattr("lib.interfaces.region_editor.messagebox.showerror", lambda *a, **k: errors.append(a))
    draw_zone(editor, 1, 0, 0, 120, 120)
    editor._save()
    assert errors and editor.winfo_exists()
    assert region_profiles.store_path().read_bytes() == before
    assert not picker.open_video(video)  # source/mode cannot change under a frozen editor
    picker.mode_var.set(MODE_LABELS[SINGLE_SEAT])
    picker._mode_changed()
    assert picker.mode_var.get() == MODE_LABELS[TABLE]


def test_seven_seat_dealer_corners_remain_draggable(ui, video):
    picker = VideoFramePicker(ui, video_path=video, profile="Legacy drag")
    picker._edit_regions()
    editor = picker.editor
    draw_zone(editor, 7, 100, 20, 200, 60)
    editor.draw_mode.set(False)
    editor._press(SimpleNamespace(x=200 * editor.scale, y=60 * editor.scale))
    editor._move(SimpleNamespace(x=220 * editor.scale, y=70 * editor.scale))
    editor._release(SimpleNamespace(x=220 * editor.scale, y=70 * editor.scale))
    editor._save()
    assert region_profiles.get_regions((320, 180), profile="Legacy drag")["dealer"] == [100, 20, 220, 70]
