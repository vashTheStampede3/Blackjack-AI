"""Region/debug preview: capture the table once, run the player model, and
draw detected cards + seat polygons + the dealer area onto the screenshot.

Pure logic (no Tkinter) — runs on a worker thread; returns a PIL image.
"""

import cv2
import numpy as np
from PIL import Image

from ..common import constants
from ..common.card_mappings import PLAYER_CLASS_MAP
from .app_modes import zones_for_mode
from .models import model_metadata
from .monitor_utils import dealer_area_rect, scaled_player_regions
from .table_inference import predict_layout, predict_table

ZONE_COLORS = {"table": (255, 120, 0), "mine": (0, 180, 255), "dealer": (0, 0, 220)}


def build_region_preview(engine, source=None, *, live_running=False) -> Image.Image:
    """Uses the engine's capture + models (shares the lazy singletons).
    Runs on a short-lived worker thread — releases its capture handle on exit."""
    cache_getter = getattr(engine, "get_region_preview_cache", None) if source is None else None
    cached = cache_getter(max_age_s=2.0) if callable(cache_getter) else None
    if source is None and live_running and cached is None:
        raise ValueError("Esperá resultado vigente de detección; no se inicia un segundo análisis.")
    model = None
    layout_metadata = None
    if cached is not None:
        frame = cached["frame"]
        preds, bounds = cached["predictions"], cached["bounds"]
        prepared_size = cached.get("input_size")
        regions = engine.regions
        dealer = getattr(engine, "_dealer_rect", None)
        card_layout = cached.get("layout") or getattr(engine, "card_layout", None)
    else:
        capture = source or engine.capture
        try:
            frame = capture.grab_bgr()
        finally:
            capture.close_local()
        model = engine.provider.players_model()
        if getattr(model, "hosted", False):
            raise ValueError("Preview requires a local model; images are not uploaded.")
        card_layout = getattr(engine, "card_layout", None)
        if source:
            regions = scaled_player_regions(capture.resolution)
            dealer = dealer_area_rect(capture.resolution)
        else:
            regions = engine.regions
            dealer = getattr(engine, "_dealer_rect", None) or dealer_area_rect(capture.resolution)
        if card_layout is not None:
            preds, layout_metadata = predict_layout(model, frame, card_layout,
                constants.PREDICTION_CONFIDENCE_PLAYERS, constants.PREDICTION_OVERLAP_PLAYERS)
            bounds = layout_metadata
            prepared_size = layout_metadata.get("size")
        else:
            preds, bounds = predict_table(model, frame, regions, dealer,
                constants.PREDICTION_CONFIDENCE_PLAYERS, constants.PREDICTION_OVERLAP_PLAYERS)
            prepared_size = [bounds[2]-bounds[0], bounds[3]-bounds[1]]

    provider = getattr(engine, "provider", None)
    metadata_peek = getattr(provider, "loaded_model_metadata", None)
    model_info = metadata_peek("players") if callable(metadata_peek) else model_metadata(model)

    img = frame.copy()
    if isinstance(bounds, dict):
        patches = bounds.get("patches", [])
        for patch in patches:
            l, t, r, b = patch["native"]
            color = ZONE_COLORS.get(patch.get("role"), (0, 220, 220))
            cv2.rectangle(img, (l, t), (r-1, b-1), color, 2)
            cv2.putText(img, f"{patch.get('role', 'input')} PATCH",
                        (l+5, max(t+16, 16)), cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1)
    else:
        l, t, r, b = bounds
        cv2.rectangle(img, (l, t), (r-1, b-1), (0, 220, 220), 2)
    model_info = model_info or model_metadata(model)
    quant = model_info.get("quantization", "unknown")
    version = model_info.get("version") or "version unknown"
    network_size = model_info.get("input_size")
    input_label = (f"prepared {prepared_size[0]}x{prepared_size[1]} px"
                   if prepared_size else "prepared size unknown")
    network_label = (f"net {network_size}x{network_size}" if network_size and model_info.get("input_size_known")
                     else f"net nominal {network_size}x{network_size}" if network_size
                     else "net input unknown")
    model_label = model_info.get("artifact") or model_info["model"]
    model_type = model_info.get("model_type") or "model type unknown"
    input_line = f"{input_label} | {network_label}"
    model_line = f"artifact {model_label} | {model_type} | {version} | quant: {quant}"
    cv2.putText(img, input_line[:115], (5, 18),
                cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 220, 220), 1)
    cv2.putText(img, model_line[:115], (5, 36),
                cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 220, 220), 1)

    # Detected cards (green boxes).
    for p in preds:
        name = PLAYER_CLASS_MAP.get(p["class"], p["class"])
        x1 = int(p["cx"] - p["width"] / 2)
        y1 = int(p["cy"] - p["height"] / 2)
        x2 = int(p["cx"] + p["width"] / 2)
        y2 = int(p["cy"] + p["height"] / 2)
        color = (0, 180, 255) if p.get("ambiguous") else (0, 200, 0)
        label = name+("?" if p.get("ambiguous") else "")
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        prepared_box = p.get("prepared_bbox")
        prepared_label = (f" prep px {tuple(round(v) for v in prepared_box)}"
                          if prepared_box else " prep px unknown")
        network_box = p.get("network_bbox")
        network_label = (f" net px {tuple(round(v) for v in network_box)}"
                         if network_box else " net px unknown")
        cv2.putText(img, f"{label} {p['confidence']:.2f}{prepared_label}{network_label}"[:110],
                    (x1, max(14, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

    # Seat polygons (blue) with seat numbers.
    if card_layout is not None:
        for role in zones_for_mode(card_layout.mode):
            polygon = getattr(card_layout, role)
            color = ZONE_COLORS[role]
            pts = np.asarray(polygon, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(img, [pts], isClosed=True, color=color, thickness=2)
            x, y = polygon[0]
            cv2.putText(img, role.upper(), (int(x)+3, max(14, int(y)+16)),
                        cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1)
    elif regions:
        for i, region in enumerate(regions):
            pts = region.vertices.astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(img, [pts], isClosed=True, color=(255, 120, 0), thickness=2)
            cx, cy = region.vertices.mean(axis=0)
            cv2.putText(img, f"P{i + 1}", (int(cx) - 14, int(cy)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 120, 0), 2)

    # Dealer area (red).
    if card_layout is None and dealer:
        left, top, right, bottom = dealer
        cv2.rectangle(img, (left, top), (right, bottom), (0, 0, 220), 2)
        cv2.putText(img, "DEALER AREA", (left + 8, top + 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 220), 2)

    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
