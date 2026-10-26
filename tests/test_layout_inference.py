"""Image preparation and coordinate checks; no real-card accuracy claims."""
from types import SimpleNamespace

import numpy as np
import pytest

from lib.logic.card_layout import CardLayout
from lib.logic.table_inference import predict_layout, prepare_layout_rois


def layout(dealer_inside=False):
    return CardLayout.from_payload({"mode": "table", "resolution": [400, 240],
        "table": [[10, 120], [310, 120], [310, 230], [10, 230]],
        "mine": [[100, 150], [180, 150], [180, 220], [100, 220]],
        "dealer": ([[220, 140], [280, 140], [280, 180], [220, 180]] if dealer_inside else
                   [[320, 10], [390, 10], [390, 60], [320, 60]])}, (400, 240))


@pytest.mark.parametrize("inside", [False, True])
def test_one_pass_native_pixels_exact_mapping_and_no_duplicate_dealer(inside):
    zones = layout(inside)
    frame = np.full((240, 400, 3), 255, np.uint8)
    frame[120:230, 10:310] = [10, 20, 30]
    rois = prepare_layout_rois(frame, zones)
    xs, ys = zip(*zones.dealer)
    positions = [(140, 180), (sum(xs)/len(xs), sum(ys)/len(ys))]
    calls = []
    def predict(image, confidence, overlap):
        role, (left, top, right, bottom), expected = rois[len(calls)]
        assert np.array_equal(image, expected)
        calls.append((image.copy(), confidence, overlap))
        return [{"cx": cx-left, "cy": cy-top, "width": 20., "height": 30.,
                 "class": "8", "confidence": .95} for cx, cy in positions
                if left <= cx < right and top <= cy < bottom] + [{"cx": -1, "cy": -1}]
    model = SimpleNamespace(hosted=False, predict=predict)
    result, metadata = predict_layout(model, frame, zones, 55, 45)
    assert len(calls) == 3 and all(call[1] <= 55 and call[2] > 45 for call in calls)
    assert len(result) == 2
    assert {(p["owner"], p["cx"], p["cy"]) for p in result} == {
        ("mine", 140, 180), ("dealer", sum(xs)/len(xs), sum(ys)/len(ys))}
    assert all(p["width"] == 20 and p["height"] == 30 for p in result)
    assert metadata["strategy"] == "roi" and len(metadata["patches"]) == 3


def test_wrong_resolution_and_remote_model_are_rejected_before_inference():
    def fail(*args):
        pytest.fail("Detector should not run")
    model = SimpleNamespace(hosted=False, predict=fail)
    with pytest.raises(ValueError, match="resolutions differ"):
        predict_layout(model, np.zeros((120, 200, 3), np.uint8), layout(), 55, 45)
    model.hosted = True
    with pytest.raises(ValueError, match="local model"):
        predict_layout(model, np.zeros((240, 400, 3), np.uint8), layout(), 55, 45)
