"""Local video frames and their saved geometry; no models or screen capture."""
import json

import cv2
import numpy as np
import pytest

from lib.common import constants
from lib.logic import monitor_utils, region_profiles
from lib.logic.video_source import VideoFile, parse_video_time


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "sample.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 180))
    assert writer.isOpened()
    for value in (20, 50, 80, 110, 140, 170):
        writer.write(np.full((180, 320, 3), value, np.uint8))
    writer.release()
    return path


def test_seek_keeps_native_size_and_freezes_selected_frame(video):
    with VideoFile(video) as reader:
        assert reader.resolution == (320, 180) and reader.fps == 10
        selected = reader.frame_at(.2)
        before = selected.grab_bgr()
        reader.frame_at(.5)
        assert selected.frame_index == 2
        assert selected.timestamp_seconds == pytest.approx(.2, abs=.001)
        assert before.shape == (180, 320, 3)
        assert np.array_equal(selected.grab_bgr(), before)
        assert 75 < before.mean() < 85
    assert np.array_equal(selected.grab_bgr(), before)


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), .6])
def test_seek_rejects_invalid_or_past_end_times(video, value):
    with VideoFile(video) as reader:
        with pytest.raises(ValueError):
            reader.frame_at(value)


def test_network_and_missing_sources_are_rejected_before_decoder(tmp_path, monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: pytest.fail("Decoder used"))
    for path in ("https://example.invalid/video.mp4", r"\\server\share\video.mp4", tmp_path / "missing.mp4"):
        with pytest.raises((ValueError, FileNotFoundError)):
            VideoFile(path)


@pytest.mark.parametrize("text,seconds", [("12.5", 12.5), ("1:02.5", 62.5), ("1:02:03", 3723)])
def test_time_entry(text, seconds):
    assert parse_video_time(text) == seconds


@pytest.mark.parametrize("text", ["-1", "nan", "inf", "1:75", "1:2:3:4", ""])
def test_invalid_time_entry(text):
    with pytest.raises(ValueError):
        parse_video_time(text)


def test_named_profile_preserves_video_reference_without_stretching(video, tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path / "profiles")
    with VideoFile(video) as reader:
        source = reader.frame_at(.3)
    players = monitor_utils.default_player_regions(source.resolution)
    dealer = (20, 20, 90, 60)
    monitor_utils.save_custom_regions(source.resolution, players, dealer,
        profile="Video sample", source_context=source.calibration_context)
    region_profiles.set_regions((640, 360), {"players": players, "dealer": list(dealer)}, profile="Other")
    saved = region_profiles.get_regions((320, 180), profile="Video sample")
    assert saved["players"] == players and saved["dealer"] == list(dealer)
    assert saved["reference_source"]["frame_index"] == 3
    assert saved["reference_source"]["native_resolution"] == [320, 180]
    assert saved["reference_source"]["crop"] == [0, 0, 320, 180]
    assert region_profiles.get_regions((640, 360), profile="Video sample") is None
    assert monitor_utils.load_custom_regions((320, 180), profile="Video sample")["dealer"] == list(dealer)
    assert json.loads(region_profiles.store_path().read_text())["active"] == "Other"
