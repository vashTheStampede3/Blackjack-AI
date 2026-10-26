"""A video replay must not disguise video calibration as a live monitor."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.logic.card_layout import CardLayout
from tools.replay_video import configure_replay


@pytest.mark.parametrize("kind,path,accepted", [
    ("video_frame", "fixture.mp4", True),
    ("video_frame", "different.mp4", False),
    ("monitor", "fixture.mp4", False),
])
def test_replay_checks_explicit_video_reference_and_uses_official_layout(monkeypatch, tmp_path, kind, path, accepted):
    from tools import replay_video as replay
    reference = {"kind": kind, "path": str(tmp_path / path)}
    layout = CardLayout.from_payload({"mode": "individual", "resolution": [400, 300],
        "mine": [[20, 150], [250, 150], [250, 280], [20, 280]],
        "dealer": [[270, 20], [390, 20], [390, 130], [270, 130]],
        "reference_source": reference}, (400, 300))
    monkeypatch.setattr(replay.region_profiles, "layout_mode", lambda *a, **k: "individual")
    monkeypatch.setattr(replay.region_profiles, "get_card_layout", lambda *a, **k: layout)
    source = SimpleNamespace(width=400, height=300, reader=SimpleNamespace(path=tmp_path / "fixture.mp4"))
    engine = SimpleNamespace(capture=SimpleNamespace(set_monitor=Mock()), use_card_layout=Mock(),
                             set_monitor=Mock())
    if not accepted:
        with pytest.raises(ValueError):
            configure_replay(engine, source, "Test", "registry")
        engine.capture.set_monitor.assert_not_called()
        engine.use_card_layout.assert_not_called()
    else:
        configure_replay(engine, source, "Test", "registry")
        engine.use_card_layout.assert_called_once_with(layout, memory="registry")
        assert engine._active_mode == "individual" and engine.regions == []
        assert engine._configuration_source_descriptor == reference
    engine.set_monitor.assert_not_called()
