"""Local, deterministic detection regression tests. No casino or result files."""
from types import SimpleNamespace
import numpy as np

from lib.common import constants
from lib.logic.monitor_utils import Polygon
from lib.logic.table_inference import predict_table, table_bounds


def test_single_crop_restores_original_coordinates_and_preserves_input(monkeypatch):
    monkeypatch.setattr(constants, "TABLE_CROP_ENABLED", 1)
    frame = np.full((1200, 1920, 3), 40, np.uint8)
    regions = [Polygon([(900, 800), (1100, 800), (1100, 1000)])]
    dealer = (900, 650, 1000, 720)
    calls = []
    def predict(image, *args):
        calls.append(image.shape)
        return [{"cx": 40., "cy": 60., "width": 20., "height": 30.,
                 "class": "A", "confidence": .95}, {"cx": -5, "cy": 0}]
    preds, bounds = predict_table(SimpleNamespace(hosted=False, predict=predict),
                                  frame, regions, dealer, 55, 45)
    l, t, r, b = bounds
    assert len(calls) == 1 and calls[0] == (b-t, r-l, 3)
    assert r-l < 1920 and b-t < 1200
    assert preds[0]["cx"] == l+40 and preds[0]["cy"] == t+60
    assert preds[0]["width"] == 20 and len(preds) == 1
    assert np.all(frame == 40)


def test_crop_includes_every_seat_and_expanded_dealer_band():
    regions = [Polygon([(100, 800), (200, 800), (200, 1000)]),
               Polygon([(1700, 800), (1800, 800), (1800, 1000)])]
    l, t, r, b = table_bounds((1200, 1920, 3), regions, (900, 650, 1000, 720))
    assert l <= 100 and r >= 1800 and t <= 650 and b >= 1000
    assert l <= 750 and r >= 1150


def test_rollback_and_hosted_keep_full_frame(monkeypatch):
    frame = np.zeros((480, 640, 3), np.uint8)
    regions = [Polygon([(100, 200), (200, 200), (200, 300)])]
    for enabled, hosted in [(0, False), (1, True)]:
        monkeypatch.setattr(constants, "TABLE_CROP_ENABLED", enabled)
        calls = []
        model = SimpleNamespace(hosted=hosted, predict=lambda image, *a: calls.append(image.shape) or [])
        _, bounds = predict_table(model, frame, regions, (200, 100, 250, 150), 55, 45)
        assert calls == [frame.shape] and bounds == (0, 0, 640, 480)
