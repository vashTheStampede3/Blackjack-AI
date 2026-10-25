"""Pure input preparation and local model metadata checks; no model runtime."""
from threading import RLock
from types import SimpleNamespace

import numpy as np
import pytest

from lib.common import constants
from lib.logic.card_layout import CardLayout
from lib.logic.image_source import ImageSource
from lib.logic.models import (ModelProvider, OnnxModel, letterbox, model_metadata,
                              onnx_input_geometry)
from lib.logic.monitor_utils import Polygon
from lib.logic.table_inference import predict_layout, predict_table, prepare_table_input


def _layout():
    return CardLayout.from_payload({"mode": "table", "resolution": [100, 80],
        "table": [[5, 30], [75, 30], [75, 75], [5, 75]],
        "mine": [[10, 40], [25, 40], [25, 70], [10, 70]],
        "dealer": [[80, 5], [95, 5], [95, 20], [80, 20]]}, (100, 80))


def test_preview_and_cycle_share_the_same_prepared_crop_and_input_pixel_boxes(monkeypatch):
    monkeypatch.setattr(constants, "TABLE_CROP_ENABLED", 1)
    frame = np.zeros((80, 100, 3), np.uint8)
    regions = [Polygon([[10, 40], [25, 40], [25, 70]])]
    dealer = (80, 5, 95, 20)
    model_calls = []
    model = SimpleNamespace(hosted=False, predict=lambda image, *args:
        model_calls.append((image.copy(), args)) or [
            {"cx": 8., "cy": 9., "width": 6., "height": 4., "class": "8"}])
    prepared, bounds = prepare_table_input(frame, model, regions, dealer)
    predicted, same_bounds = predict_table(model, frame, regions, dealer, 50, 40)
    assert len(model_calls) == 1
    assert np.array_equal(model_calls[0][0], prepared)
    assert bounds == same_bounds
    assert predicted[0]["input_size"] == [prepared.shape[1], prepared.shape[0]]
    assert predicted[0]["prepared_bbox"] == [5., 7., 11., 11.]
    assert predicted[0]["network_bbox"] is None  # a stub has no known preprocessing
    assert predicted[0]["cx"] == 8 + bounds[0]


def test_layout_predictions_keep_input_scale_bbox_and_native_center_once():
    frame = np.zeros((80, 100, 3), np.uint8)
    zones = _layout()
    calls = []
    def predict_inside(image, *args):
        calls.append(image.shape)
        # crop packing order is table then dealer, so derive table patch origin.
        return [{"cx": 12., "cy": 15., "width": 6., "height": 8., "class": "8"}]
    results, metadata = predict_layout(SimpleNamespace(hosted=False, predict=predict_inside),
                                       frame, zones, 50, 40)
    assert len(calls) == 1
    assert results[0]["prepared_bbox"] == [9., 11., 15., 19.]
    assert results[0]["network_bbox"] is None
    assert results[0]["input_size"] == metadata["size"]


def test_model_metadata_is_a_loaded_local_peek_and_unknown_stays_unknown():
    model = object.__new__(OnnxModel)
    model.name = "Local ONNX"
    model.hosted = False
    model._model_path = "cards.onnx"
    model._size = 640
    model._artifact_metadata = {"model_id": "project/17", "quantization": "QDQ U8S8"}
    info = model_metadata(model)
    assert info["artifact"] == "cards.onnx"
    assert info["version"] == "project/17"
    assert info["quantization"] == "QDQ U8S8"
    assert info["input_size"] == 640
    assert model_metadata(None)["quantization"] == "unknown"

    provider = object.__new__(ModelProvider)
    provider._lock = RLock()
    provider._players = None
    provider._dealer = None
    assert provider.loaded_model_metadata()["model"] == "not loaded"
    assert provider._players is None


def test_onnx_network_box_geometry_accounts_for_letterbox_pad_exactly():
    geometry = onnx_input_geometry(1280, 720, 640, "letterbox")
    assert geometry == {"known": True, "input_size": [640, 640],
                        "scale": [.5, .5], "pad": [0, 140], "mode": "letterbox"}
    prepared, ratio, pad = letterbox(np.zeros((720, 1280, 3), np.uint8), 640)
    assert prepared.shape == (640, 640, 3)
    assert (ratio, list(pad)) == (.5, [0, 140])
    # Stub an already loaded model object without ONNX Runtime or weights.
    # Its predict method returns the prepared-image pixel coordinates as a
    # model backend normally does; table inference maps them exactly once.
    model = object.__new__(OnnxModel)
    model._size = 640
    model._resize_mode = "letterbox"
    model.hosted = False
    model.predict = lambda image, *args: [{"cx": 300., "cy": 200.,
        "width": 200., "height": 200., "class": "8", "confidence": .9}]
    predictions, bounds = predict_table(model, np.zeros((720, 1280, 3), np.uint8),
                                        [], None, 50, 40)
    assert bounds == (0, 0, 1280, 720)
    assert predictions[0]["input_size"] == [1280, 720]
    assert predictions[0]["prepared_bbox"] == [200., 100., 400., 300.]
    assert predictions[0]["network_bbox"] == [100., 190., 200., 290.]
    assert predictions[0]["cx"] == 300. and predictions[0]["cy"] == 200.
    assert onnx_input_geometry(1280, 720, 640, "Stretch to")["pad"] == [0, 0]


def test_offline_image_source_exposes_the_single_crop_resize_transform():
    source = ImageSource(np.zeros((40, 80, 3), np.uint8), resolution=(40, 20))
    source.set_crop((20, 10, 60, 30))
    frame = source.grab_bgr()
    assert frame.shape == (20, 40, 3)
    assert source.last_capture_metadata == {
        "source_size": [80, 40], "crop": [20, 10, 60, 30],
        "crop_size": [40, 20], "output_size": [40, 20], "scale": [1., 1.],
    }


def test_live_preview_reuses_fresh_engine_detection_without_loading_or_predicting():
    from lib.logic.region_preview import build_region_preview
    frame = np.zeros((40, 60, 3), np.uint8)
    cache = {"frame": frame, "predictions": [{
        "class": "8", "cx": 20., "cy": 20., "width": 8., "height": 12.,
        "confidence": .9, "prepared_bbox": [16., 14., 24., 26.],
    }], "bounds": (0, 0, 60, 40), "input_size": [60, 40]}
    provider = SimpleNamespace(
        players_model=lambda: pytest.fail("cache hit must not load or infer"),
        loaded_model_metadata=lambda kind: {"model": "Local test", "version": None,
            "quantization": "unknown", "input_size": None, "input_size_known": None})
    engine = SimpleNamespace(get_region_preview_cache=lambda **kwargs: cache,
                             provider=provider, regions=[], _dealer_rect=(0, 0, 20, 20))
    result = build_region_preview(engine, live_running=True)
    assert result.size == (60, 40)


def test_live_preview_cache_miss_never_captures_or_runs_another_detector():
    from lib.logic.region_preview import build_region_preview
    fail = lambda *args, **kwargs: pytest.fail("live cache miss must not capture or infer")
    engine = SimpleNamespace(get_region_preview_cache=lambda **kwargs: None,
        capture=SimpleNamespace(grab_bgr=fail, close_local=fail),
        provider=SimpleNamespace(players_model=fail))
    with pytest.raises(ValueError, match="resultado vigente"):
        build_region_preview(engine, live_running=True)


def test_offline_layout_preview_uses_same_packed_preparation_once():
    from lib.logic.region_preview import build_region_preview
    source = ImageSource(np.zeros((80, 100, 3), np.uint8))
    calls = []
    model = SimpleNamespace(hosted=False, name="Fake local", predict=lambda image, *args:
        calls.append((image.shape, args)) or [{
            "class": "8", "cx": 12., "cy": 15., "width": 6., "height": 8.,
            "confidence": .9, "prepared_bbox": [9., 11., 15., 19.]}])
    engine = SimpleNamespace(capture=source, card_layout=_layout(), regions=[],
                             provider=SimpleNamespace(players_model=lambda: model))
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("lib.logic.region_preview.scaled_player_regions", lambda *_: [])
        monkeypatch.setattr("lib.logic.region_preview.dealer_area_rect", lambda *_: (80, 5, 95, 20))
        result = build_region_preview(engine, source=source)
    assert len(calls) == 1
    assert calls[0][0] == (68, 70, 3)
    assert result.size == (100, 80)
