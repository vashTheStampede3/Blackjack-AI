"""Uniform adapters. All boxes are ROI-local xyxy, confidence in [0,1]."""
import base64
import os
from dataclasses import asdict, dataclass

import cv2
import numpy as np
import requests

from tools.common import ROOT
from lib.logic.models import OnnxModel
from lib.logic.vision_labels import canonical_rank


@dataclass
class Detection:
    rank: str
    box: list
    confidence: float
    code: str = ""

    def asdict(self):
        return asdict(self)


def iou(a, b):
    inter = max(0, min(a[2],b[2])-max(a[0],b[0])) * max(0, min(a[3],b[3])-max(a[1],b[1]))
    aa, ab = max(0,a[2]-a[0])*max(0,a[3]-a[1]), max(0,b[2]-b[0])*max(0,b[3]-b[1])
    return inter / max(1e-9, aa+ab-inter)


def dedupe(items, threshold=.5):
    kept = []
    for item in sorted(items, key=lambda p:p.confidence, reverse=True):
        if not any(iou(item.box, prior.box) > threshold for prior in kept):
            kept.append(item)
    return kept


def group_pips(items, distance):
    """Conservative one-to-one same-code pairs; never chain whole fanned hands.

    distance is a measured native-pixel card diagonal, configured per table.
    This heuristic must be validated on repeated identical cards in holdout.
    """
    items = dedupe(items)
    result, used = [], set()
    for i, item in enumerate(items):
        if i in used:
            continue
        a = np.array([(item.box[0]+item.box[2])/2,(item.box[1]+item.box[3])/2])
        options = []
        for j, other in enumerate(items):
            if j <= i or j in used or item.code != other.code or not item.code:
                continue
            b = np.array([(other.box[0]+other.box[2])/2,(other.box[1]+other.box[3])/2])
            dist = float(np.linalg.norm(a-b))
            if dist <= distance:
                options.append((dist,j))
        if options:
            _, j = min(options)
            other = items[j]
            box = [min(item.box[0],other.box[0]),min(item.box[1],other.box[1]),
                   max(item.box[2],other.box[2]),max(item.box[3],other.box[3])]
            result.append(Detection(item.rank, box, max(item.confidence,other.confidence), item.code))
            used.add(j)
        else:
            result.append(item)
        used.add(i)
    return result


def starts(length, tile, overlap):
    if length <= tile:
        return [0]
    values = list(range(0,length-tile+1,max(1,int(tile*(1-overlap)))))
    if values[-1] != length-tile:
        values.append(length-tile)
    return values


class LocalDetector:
    def __init__(self, weights, confidence=.5, tile=0, overlap=.25, upscale=1., pip_distance=0):
        self.model = OnnxModel(ROOT/weights)
        self.confidence, self.tile, self.overlap = confidence, tile, overlap
        self.upscale, self.pip_distance = upscale, pip_distance

    def predict(self, frame):
        h,w = frame.shape[:2]
        tile = self.tile or max(h,w)
        items = []
        for y in starts(h,tile,self.overlap):
            for x in starts(w,tile,self.overlap):
                crop = frame[y:min(y+tile,h),x:min(x+tile,w)]
                ch,cw = crop.shape[:2]
                if self.upscale != 1:
                    crop = cv2.resize(crop,None,fx=self.upscale,fy=self.upscale,interpolation=cv2.INTER_CUBIC)
                sy,sx = crop.shape[0]/ch,crop.shape[1]/cw
                for p in self.model.predict(crop,self.confidence*100,45):
                    cx,cy,bw,bh = p["cx"]/sx+x,p["cy"]/sy+y,p["width"]/sx,p["height"]/sy
                    if not (0<=cx<w and 0<=cy<h):
                        continue
                    items.append(Detection(canonical_rank(p["class"]),
                        [max(0,cx-bw/2),max(0,cy-bh/2),min(w,cx+bw/2),min(h,cy+bh/2)],
                        p["confidence"],p["class"]))
        items = dedupe(items)
        return group_pips(items,self.pip_distance) if self.pip_distance else items


class HostedDetector:
    """Only accepts explicitly redacted ROI frames from a recorded manifest."""
    def __init__(self, model_id, confidence=.5):
        self.model_id, self.confidence = model_id, confidence
        self.key = os.environ.get("ROBOFLOW_API_KEY", "")
        if not self.key:
            raise ValueError("ROBOFLOW_API_KEY is missing")

    def predict(self, frame):
        ok, encoded = cv2.imencode(".jpg",frame,[cv2.IMWRITE_JPEG_QUALITY,95])
        if not ok:
            raise ValueError("ROI encoding failed")
        try:
            response = requests.post("https://detect.roboflow.com/"+self.model_id,
                params={"api_key":self.key,"confidence":int(self.confidence*100),"overlap":45},
                data=base64.b64encode(encoded).decode("ascii"),
                headers={"Content-Type":"application/x-www-form-urlencoded"},
                timeout=(5,20),allow_redirects=False)
        except requests.RequestException:
            raise RuntimeError("Roboflow connection failed (URL and credential suppressed)") from None
        if response.status_code != 200:
            raise RuntimeError(f"Roboflow HTTP {response.status_code}; credential and response suppressed")
        return [Detection(canonical_rank(p["class"]),
            [p["x"]-p["width"]/2,p["y"]-p["height"]/2,p["x"]+p["width"]/2,p["y"]+p["height"]/2],
            float(p["confidence"]),p["class"]) for p in response.json().get("predictions",[])]


def build(args):
    if args.hosted:
        return HostedDetector(args.hosted,args.confidence)
    return LocalDetector(args.weights,args.confidence,args.tile,args.overlap,args.upscale,args.pip_distance)


def add_arguments(parser):
    parser.add_argument("--weights", default="models/player_cards.onnx")
    parser.add_argument("--hosted", help="Explicit opt-in: model/version; redacted ROI only")
    parser.add_argument("--confidence", type=float, default=.5)
    parser.add_argument("--tile", type=int, default=0)
    parser.add_argument("--overlap", type=float, default=.25)
    parser.add_argument("--upscale", type=float, default=1)
    parser.add_argument("--pip-distance", type=float, default=0)
