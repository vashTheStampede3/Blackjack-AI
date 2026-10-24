"""Single region-union crop; detector outputs stay in original frame pixels."""
import math

from ..common import constants


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


def predict_table(model, frame, regions, dealer, confidence, overlap):
    h, w = frame.shape[:2]
    bounds = table_bounds(frame.shape, regions, dealer) if (
        constants.TABLE_CROP_ENABLED and not getattr(model, "hosted", False)) else (0, 0, w, h)
    l, t, r, b = bounds
    predictions = model.predict(frame[t:b, l:r].copy(), confidence, overlap)
    restored = [p | {"cx": p["cx"]+l, "cy": p["cy"]+t} for p in predictions
                if 0 <= p["cx"] < r-l and 0 <= p["cy"] < b-t]
    return restored, bounds
