"""Privacy boundary shared by capture, remote inference and evaluation."""
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_config(path):
    cfg = read_json(path)
    roi = cfg.get("roi")
    if not roi or len(roi) != 4:
        raise ValueError("Configure roi=[left,top,right,bottom] in host pixels first.")
    l, t, r, b = map(int, roi)
    if l < 0 or t < 0 or r <= l or b <= t:
        raise ValueError("Invalid ROI geometry")
    if not cfg.get("geometry_verified") or not cfg.get("privacy_reviewed"):
        raise ValueError("ROI geometry and privacy masks must be reviewed first.")
    if cfg.get("table_kind") in ("infinite", "virtual", "csm"):
        raise ValueError("Counting is inapplicable to the configured table kind.")
    for mask in cfg.get("masks", []):
        if len(mask) != 4 or not (0 <= mask[0] < mask[2] <= 1 and 0 <= mask[1] < mask[3] <= 1):
            raise ValueError("Masks must be normalized ROI-local xyxy rectangles.")
    return cfg


def redact(image, cfg):
    out = image.copy()
    h, w = out.shape[:2]
    for l, t, r, b in cfg.get("masks", []):
        out[int(t*h):int(np.ceil(b*h)), int(l*w):int(np.ceil(r*w))] = 0
    return out


def capture_roi(sct, cfg):
    l, t, r, b = map(int, cfg["roi"])
    monitor = sct.monitors[int(cfg.get("monitor", 1))]
    expected = cfg.get("host_resolution")
    if expected != [monitor["width"], monitor["height"]]:
        raise ValueError("Host resolution changed: recalibrate ROI before recording.")
    if r > monitor["width"] or b > monitor["height"]:
        raise ValueError("ROI exceeds the selected monitor")
    # Capture only the table ROI, never a full screenshot on this path.
    shot = sct.grab({"left": monitor["left"]+l, "top": monitor["top"]+t,
                     "width": r-l, "height": b-t})
    return redact(np.asarray(shot)[:, :, :3], cfg)


def digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def table_anchors_match(image,cfg):
    """Reject black screens and window occlusion before any frame is saved.

    Anchors are reviewed, redacted, static felt/logo crops, not account HUD.
    """
    anchors=cfg.get("anchors",[])
    if not anchors:
        return False
    matched=0
    for anchor in anchors:
        template=cv2.imread(str(ROOT/anchor["image"]))
        l,t,r,b=map(int,anchor["rect"])
        patch=image[t:b,l:r]
        if template is None or template.shape!=patch.shape or not patch.size or template.std()<8:
            continue
        score=float(cv2.matchTemplate(patch,template,cv2.TM_CCOEFF_NORMED)[0,0])
        matched+=score>=float(anchor.get("threshold",.85))
    return matched>=int(cfg.get("min_anchors_matched",len(anchors)))


def change_score(previous, current):
    thumb = cv2.resize(cv2.cvtColor(current, cv2.COLOR_BGR2GRAY), (160, 96))
    if previous is None:
        return thumb, float("inf")
    delta = cv2.absdiff(previous, thumb).astype(np.float32)
    # Local changes such as one flipped card survive global averaging.
    return thumb, float(delta.reshape(6, 16, 10, 16).mean(axis=(1, 3)).max())


def manifest(path):
    path = Path(path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows
