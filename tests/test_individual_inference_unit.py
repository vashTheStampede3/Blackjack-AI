"""Synthetic image and isolated-method tests; no capture, runtime or workers."""
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from lib.logic.card_layout import CardLayout
from lib.logic.engine import DetectionEngine
from lib.logic.monitor_utils import Polygon
from lib.logic import region_preview
from lib.logic.table_inference import inference_regions, prepare_layout_image, prepare_layout_rois, predict_layout


def rect(l, t, r, b):
    return [[l, t], [r, t], [r, b], [l, b]]


def layout(mode="individual", inside=False, concave=False, tall=False):
    data = {"mode": mode, "resolution": [400, 240],
            "mine": rect(20, 120, 100, 200), "dealer": rect(300, 10, 360, 60)}
    if tall:
        data["mine"] = rect(20, 100, 50, 220)
        data["dealer"] = rect(300, 10, 330, 90)
    if concave:
        data["mine"] = [[20, 120], [100, 120], [100, 140],
                        [50, 140], [50, 200], [20, 200]]
    if mode == "multiplayer":
        data["table"] = rect(10, 100, 390, 230)
        if inside:
            data["dealer"] = rect(250, 120, 310, 170)
    return CardLayout.from_payload(data, (400, 240))


class FakeModel:
    hosted = False

    def __init__(self, predictions):
        self.predictions = predictions
        self.last_input = None
        self.calls = 0

    def predict(self, image, confidence, overlap):
        self.calls += 1
        self.last_input = image.copy()
        return [dict(p) for p in self.predictions]


class RoiModel(FakeModel):
    def __init__(self, frame, zones, predictions):
        super().__init__(predictions)
        self.rois = prepare_layout_rois(frame, zones)
        self.inputs = []

    def predict(self, image, confidence, overlap):
        role, (left, top, right, bottom), expected = self.rois[self.calls % len(self.rois)]
        assert np.array_equal(image, expected)
        self.inputs.append(image.copy())
        self.calls += 1
        return [dict(p, cx=p['cx']-left, cy=p['cy']-top) for p in self.predictions
                if left <= p['cx'] < right and top <= p['cy'] < bottom]


def native_prediction(cx, cy):
    return {'class': '8', 'confidence': .95, 'width': 8., 'height': 12.,
            'cx': cx, 'cy': cy}


def prediction(patch, cx, cy):
    l, t, _, _ = patch["native"]
    x, y, _, _ = patch["packed"]
    return {"class": "8", "confidence": .95, "width": 8., "height": 12.,
            "cx": cx-l+x, "cy": cy-t+y}


@pytest.mark.parametrize("mode,roles", [
    ("individual", ["mine", "dealer"]), ("multiplayer", ["table", "dealer"]),
])
def test_selected_regions(mode, roles):
    zones = layout(mode)
    selected = inference_regions(zones)
    assert [role for role, _ in selected] == roles
    assert all(polygon is getattr(zones, role) for role, polygon in selected)


def test_invalid_mode():
    with pytest.raises(ValueError, match="Modo desconocido"):
        inference_regions(SimpleNamespace(mode="unknown"))


@pytest.mark.parametrize("mode,missing", [
    ("individual", "mine"), ("individual", "dealer"),
    ("multiplayer", "table"), ("multiplayer", "dealer"),
])
def test_missing_region_is_clear(mode, missing):
    values = dict(vars(layout(mode)))
    values[missing] = None
    with pytest.raises(ValueError, match=f"Falta la región {missing}"):
        inference_regions(SimpleNamespace(**values))


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("tall", [False, True])
def test_mask_crop_packing_gap_and_immutable_frame(mode, tall):
    zones = layout(mode, concave=True, tall=tall)
    frame = np.full((240, 400, 3), 123, np.uint8)
    original = frame.copy()
    packed, patches = prepare_layout_image(frame, zones)
    assert len(patches) == 2
    occupied = np.zeros(packed.shape[:2], bool)
    for patch, (role, polygon) in zip(patches, inference_regions(zones)):
        assert patch["role"] == role
        assert set(patch) == {"role", "native", "packed"}
        l, t, r, b = patch["native"]
        assert [l, t, r, b] == [min(x for x, _ in polygon), min(y for _, y in polygon),
                                max(x for x, _ in polygon), max(y for _, y in polygon)]
        x, y, right, bottom = patch["packed"]
        assert (right-x, bottom-y) == (r-l, b-t)
        expected = np.zeros((b-t, r-l), np.uint8)
        cv2.fillPoly(expected, [np.array(polygon, np.int32)-[l, t]], 255)
        assert np.array_equal(packed[y:bottom, x:right], np.repeat(expected[:, :, None] > 0, 3, axis=2)*123)
        occupied[y:bottom, x:right] = True
    assert np.all(packed[~occupied] == 0)
    assert (~occupied).any()
    assert packed.shape[1] == max(p["packed"][2] for p in patches)
    assert packed.shape[0] == max(p["packed"][3] for p in patches)
    assert np.array_equal(frame, original)


def test_dealer_hole_mask_and_no_duplicate_acceptance():
    zones = layout("multiplayer", inside=True)
    frame = np.full((240, 400, 3), 255, np.uint8)
    model = RoiModel(frame, zones, [native_prediction(280, 140)])
    results, _ = predict_layout(model, frame, zones, 55, 45)
    assert len(results) == 1 and results[0]["owner"] == "dealer"


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("tall", [False, True])
def test_predictions_restore_native_once_preserve_boxes_and_equal_ranks(mode, tall):
    zones = layout(mode, tall=tall)
    frame = np.full((240, 400, 3), 80, np.uint8)
    points = [(30, 130, "mine"), (310, 30, "dealer")]
    if mode == "multiplayer":
        points.append((200, 180, "other"))
    model = RoiModel(frame, zones, [native_prediction(x, y) for x, y, _ in points])
    results, metadata = predict_layout(model, frame, zones, 55, 45)
    assert model.calls == (2 if mode == "individual" else 3)
    assert metadata["strategy"] == "roi"
    assert len(results) == len(points)
    assert {(p["cx"], p["cy"], p["owner"]) for p in results} == set(points)
    for result in results:
        left, top, _, _ = next(bounds for role, bounds, _ in model.rois
                              if role == result["roi_role"])
        x, y = result["cx"]-left, result["cy"]-top
        assert result["prepared_bbox"] == [x-4, y-6, x+4, y+6]
        assert result["width"] == 8 and result["height"] == 12
        assert result["network_bbox"] is None
    assert np.all(frame == 80)


@pytest.mark.parametrize("tall", [False, True])
def test_individual_discards_polygon_exterior_and_gap(tall):
    zones = layout(concave=True, tall=tall)
    frame = np.zeros((240, 400, 3), np.uint8)
    model = RoiModel(frame, zones, [native_prediction(80, 180), native_prediction(30, 130),
                                   native_prediction(12, 112)])
    results, _ = predict_layout(model, frame, zones, 55, 45)
    assert [p["owner"] for p in results] == ["mine"]


@pytest.mark.parametrize("hosted", [False, True])
def test_rejects_wrong_resolution_or_hosted_before_model_call(hosted):
    model = FakeModel([])
    model.hosted = hosted
    with pytest.raises(ValueError, match="local model" if hosted else "resolutions differ"):
        predict_layout(model, np.zeros((120, 200, 3), np.uint8), layout(), 55, 45)
    assert model.calls == 0


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("cached", [False, True])
def test_preview_regions_patches_native_boxes_and_cache(monkeypatch, mode, cached):
    zones = layout(mode)
    frame = np.full((240, 400, 3), 40, np.uint8)
    rois = prepare_layout_rois(frame, zones)
    patches = [{"role": role, "native": list(bounds),
                "input_size": [image.shape[1], image.shape[0]]} for role, bounds, image in rois]
    native = native_prediction(30, 130)
    model = RoiModel(frame, zones, [native])
    metadata = {"strategy": "roi", "size": [400, 240], "patches": patches}
    capture = SimpleNamespace(grab_bgr=Mock(return_value=frame), close_local=Mock(), resolution=(400, 240))
    provider = SimpleNamespace(players_model=Mock(return_value=model),
                               loaded_model_metadata=lambda _: {"model": "fake"})
    cache = {"frame": frame, "predictions": [native], "bounds": metadata,
             "input_size": metadata["size"], "layout": zones}
    engine = SimpleNamespace(card_layout=zones, regions=[], _dealer_rect=(300, 10, 360, 60),
                             capture=capture, provider=provider,
                             get_region_preview_cache=lambda **_: cache if cached else None)
    lines, texts, boxes = [], [], []
    monkeypatch.setattr(region_preview.cv2, "polylines", lambda img, pts, **kw: lines.append(pts[0].copy()))
    monkeypatch.setattr(region_preview.cv2, "putText", lambda img, text, *a: texts.append(text))
    monkeypatch.setattr(region_preview.cv2, "rectangle", lambda img, a, b, color, *args: boxes.append((a, b, color)))
    result = region_preview.build_region_preview(engine, live_running=cached)
    expected_roles = ["mine", "dealer"] if mode == "individual" else ["table", "mine", "dealer"]
    assert len(lines) == len(expected_roles)
    for poly, role in zip(lines, expected_roles):
        assert np.array_equal(poly.reshape(-1, 2), getattr(zones, role))
        assert role.upper() in texts
    assert ("TABLE" in texts) == (mode == "multiplayer")
    for patch in patches:
        assert f"{patch['role']} PATCH" in texts
        l, t, r, b = patch["native"]
        assert ((l, t), (r-1, b-1), region_preview.ZONE_COLORS[patch["role"]]) in boxes
    assert ((26, 124), (34, 136), (0, 200, 0)) in boxes
    assert result.size == (400, 240) and np.all(frame == 40)
    assert model.calls == (0 if cached else len(rois))
    assert provider.players_model.call_count == (0 if cached else 1)
    assert capture.grab_bgr.call_count == (0 if cached else 1)
    assert capture.close_local.call_count == (0 if cached else 1)


def test_live_preview_cache_miss_never_captures_or_loads():
    fail = Mock(side_effect=AssertionError("must not run"))
    engine = SimpleNamespace(get_region_preview_cache=lambda **_: None,
                             capture=SimpleNamespace(grab_bgr=fail),
                             provider=SimpleNamespace(players_model=fail))
    with pytest.raises(ValueError, match="resultado vigente"):
        region_preview.build_region_preview(engine, live_running=True)
    fail.assert_not_called()


@pytest.mark.parametrize("mode", ["individual", "multiplayer", None])
@pytest.mark.parametrize("change", ["player", "dealer"])
def test_frame_change_watches_correct_regions_and_updates_history(mode, change):
    zones = layout(mode) if mode else None
    engine = SimpleNamespace(card_layout=zones, regions=[] if mode else [Polygon(rect(20, 120, 100, 200))],
                             _dealer_rect=(300, 10, 360, 60), _prev_thumb=None,
                             _prev_dealer_thumb=None, _prev_seat_thumbs={}, _skipped_cycles=0)
    frame = np.zeros((240, 400, 3), np.uint8)
    check = lambda: DetectionEngine._frame_unchanged(engine, frame)
    assert check() is False
    assert check() is True
    old = engine._prev_seat_thumbs[0].copy()
    if change == "dealer":
        frame[10:60, 300:360] = 255
    elif mode == "multiplayer":
        frame[100:230, 150:390] = 255  # outside mine, inside table
    else:
        frame[120:200, 20:100] = 255
    assert check() is False
    assert check() is True
    assert engine._prev_thumb.shape == (54, 96)
    assert engine._prev_dealer_thumb.shape == (27, 48)
    assert engine._prev_seat_thumbs[0].shape == (32, 48)
    assert np.array_equal(old, engine._prev_seat_thumbs[0]) == (change == "dealer")
    assert engine._skipped_cycles == 1


def test_frame_change_rejects_invalid_mode():
    engine = SimpleNamespace(card_layout=SimpleNamespace(mode="invalid"), regions=[],
                             _dealer_rect=None, _prev_thumb=None,
                             _prev_dealer_thumb=None, _prev_seat_thumbs={})
    with pytest.raises(ValueError, match="Modo desconocido"):
        DetectionEngine._frame_unchanged(engine, np.zeros((240, 400, 3), np.uint8))
