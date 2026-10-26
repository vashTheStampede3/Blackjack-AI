"""Native ROI inference; detector outputs stay in original frame pixels."""
import math

import cv2
import numpy as np

from ..common import constants
from .app_modes import INDIVIDUAL, MULTIPLAYER
from .models import model_input_geometry


def table_bounds(shape, regions, dealer):
    h, w = shape[:2]
    if not regions or dealer is None:
        return 0, 0, w, h
    # Include every seat, not merely the seats marked 'mine'. Preserve the
    # existing dealer horizontal search margin so no accepted area is lost.
    l, t, r, b = dealer
    pad = max(r-l, int(200*w/constants.BASE_RESOLUTION[0]))
    bounds = [(l-pad, t, r+pad, b)] + [region.bounds for region in regions]
    margin = max(8, round(min(w, h)*.015))
    left = max(0, math.floor(min(v[0] for v in bounds))-margin)
    top = max(0, math.floor(min(v[1] for v in bounds))-margin)
    right = min(w, math.ceil(max(v[2] for v in bounds))+margin)
    bottom = min(h, math.ceil(max(v[3] for v in bounds))+margin)
    return (left, top, right, bottom) if right > left and bottom > top else (0, 0, w, h)


def prepare_table_input(frame, model, regions, dealer):
    """Shared frame preparation used by preview and the live detection cycle."""
    h, w = frame.shape[:2]
    bounds = table_bounds(frame.shape, regions, dealer) if (
        constants.TABLE_CROP_ENABLED and not getattr(model, "hosted", False)) else (0, 0, w, h)
    left, top, right, bottom = bounds
    return frame[top:bottom, left:right].copy(), bounds


def _input_box(prediction):
    cx, cy = float(prediction["cx"]), float(prediction["cy"])
    width, height = float(prediction.get("width", 0)), float(prediction.get("height", 0))
    return [cx-width/2, cy-height/2, cx+width/2, cy+height/2]


def _box_scale(prediction, input_size, geometry):
    """Record exact prepared pixels and known model-network pixels separately."""
    prepared_bbox = _input_box(prediction)
    result = {"prepared_bbox": prepared_bbox, "input_size": list(input_size),
              "network_bbox": None, "network_input_size": None,
              "network_transform_known": bool(geometry.get("known"))}
    if not geometry.get("known"):
        return result
    sx, sy = geometry["scale"]
    px, py = geometry["pad"]
    result["network_bbox"] = [prepared_bbox[0]*sx+px, prepared_bbox[1]*sy+py,
                              prepared_bbox[2]*sx+px, prepared_bbox[3]*sy+py]
    result["network_input_size"] = geometry["input_size"]
    result["network_transform"] = {k: geometry[k] for k in ("scale", "pad", "mode")}
    return result


def predict_table(model, frame, regions, dealer, confidence, overlap):
    input_image, bounds = prepare_table_input(frame, model, regions, dealer)
    l, t, r, b = bounds
    predictions = model.predict(input_image, confidence, overlap)
    input_size = [input_image.shape[1], input_image.shape[0]]
    geometry = model_input_geometry(model, *input_size)
    restored = [p | _box_scale(p, input_size, geometry) |
                    {"cx": p["cx"]+l, "cy": p["cy"]+t} for p in predictions
                if 0 <= p["cx"] < r-l and 0 <= p["cy"] < b-t]
    return restored, bounds


def inference_regions(layout):
    """Select the two model patches for an official calibration mode."""
    if layout.mode == INDIVIDUAL:
        regions = (("mine", layout.mine), ("dealer", layout.dealer))
    elif layout.mode == MULTIPLAYER:
        regions = (("table", layout.table), ("dealer", layout.dealer))
    else:
        raise ValueError(f"Modo desconocido: {layout.mode}")
    for role, polygon in regions:
        if polygon is None:
            raise ValueError(f"Falta la región {role} para el modo {layout.mode}")
    return regions


def prepare_layout_image(frame, layout):
    """Pack selected regions without resizing or including the UI between them.

    The players' region and dealer occupy separate masked patches. If dealer lies
    in Mesa, those pixels are excluded from the table patch to avoid duplication.
    All returned coordinates can be mapped back to native frame pixels once.
    """
    if (frame.shape[1], frame.shape[0]) != layout.resolution:
        raise ValueError("Frame and layout resolutions differ; no automatic stretching")
    pieces = []
    for role, polygon in inference_regions(layout):
        left = max(0, math.floor(min(p[0] for p in polygon)))
        top = max(0, math.floor(min(p[1] for p in polygon)))
        right = min(frame.shape[1], math.ceil(max(p[0] for p in polygon)))
        bottom = min(frame.shape[0], math.ceil(max(p[1] for p in polygon)))
        image = frame[top:bottom, left:right].copy()
        mask = np.zeros(image.shape[:2], np.uint8)
        def local_points(poly):
            return np.rint([(x-left, y-top) for x, y in poly]).astype(np.int32)
        cv2.fillPoly(mask, [local_points(polygon)], 255)
        if role == "table":
            cv2.fillPoly(mask, [local_points(layout.dealer)], 0)
        image[mask == 0] = 0
        pieces.append((role, (left, top, right, bottom), image))
    gap = 8
    widths = [image.shape[1] for _, _, image in pieces]
    heights = [image.shape[0] for _, _, image in pieces]
    vertical = max(max(widths), sum(heights)+gap) <= max(sum(widths)+gap, max(heights))
    width = max(widths) if vertical else sum(widths)+gap
    height = sum(heights)+gap if vertical else max(heights)
    packed = np.zeros((height, width, 3), np.uint8)
    patches, offset = [], 0
    for role, native, image in pieces:
        x, y = (0, offset) if vertical else (offset, 0)
        h, w = image.shape[:2]
        packed[y:y+h, x:x+w] = image
        patches.append({"role": role, "native": list(native), "packed": [x, y, x+w, y+h]})
        offset += (h if vertical else w) + gap
    return packed, patches


def prepare_layout_rois(frame, layout, padding=8):
    """Independent native crops, including context around calibrated edges.

    Pixels are never scaled or packed here. Ownership is checked against the
    original polygons after inference, so padding never expands a player's zone.
    Multiplayer adds a dedicated mine pass to the wider table pass.
    """
    if (frame.shape[1], frame.shape[0]) != layout.resolution:
        raise ValueError("Frame and layout resolutions differ; no automatic stretching")
    regions = list(inference_regions(layout))
    if layout.mode == MULTIPLAYER:
        regions.insert(1, ("mine", layout.mine))
    result = []
    for role, polygon in regions:
        if polygon is None:
            raise ValueError(f"Falta la region {role} para el modo {layout.mode}")
        pad = max(0, int(padding))
        left = max(0, math.floor(min(x for x, _ in polygon)) - pad)
        top = max(0, math.floor(min(y for _, y in polygon)) - pad)
        right = min(frame.shape[1], math.ceil(max(x for x, _ in polygon)) + pad)
        bottom = min(frame.shape[0], math.ceil(max(y for _, y in polygon)) + pad)
        result.append((role, (left, top, right, bottom),
                       frame[top:bottom, left:right].copy()))
    return result


def predict_layout(model, frame, layout, confidence, overlap):
    if getattr(model, "hosted", False):
        raise ValueError("Table-layout replay requires a local model")
    restored, patches = [], []
    for role, bounds, image in prepare_layout_rois(frame, layout):
        left, top, right, bottom = bounds
        input_size = [right-left, bottom-top]
        patches.append({"role": role, "native": list(bounds), "input_size": input_size})
        geometry = model_input_geometry(model, *input_size)
        # Keep weak observations visible for review; they cannot confirm a
        # rank below the configured acceptance threshold. Higher NMS IoU keeps
        # overlapping cards available to the physical-identity stage.
        predictions = model.predict(image, min(confidence, 35), max(overlap, 80))
        for p in predictions:
            if not (0 <= p["cx"] < right-left and 0 <= p["cy"] < bottom-top):
                continue
            cx, cy = p["cx"]+left, p["cy"]+top
            owner = layout.owner_at(cx, cy)
            if not ((role == "mine" and owner == "mine")
                    or (role == "dealer" and owner == "dealer")
                    or (role == "table" and owner == "other")):
                continue
            candidate = p | _box_scale(p, input_size, geometry) | {
                "cx": cx, "cy": cy, "owner": owner, "roi_role": role,
                "ambiguous": bool(p.get("ambiguous")
                                  or p.get("confidence", 0) < confidence/100.)}
            # A native point has exactly one primary pass: mine, dealer or
            # other/table. Exclude table proposals from mine/dealer, so border
            # deduplication cannot collapse two overlapping cards by distance.
            restored.append(candidate)
    size = [max(p["input_size"][i] for p in patches) for i in (0, 1)]
    return restored, {"strategy": "roi", "size": size, "patches": patches}
