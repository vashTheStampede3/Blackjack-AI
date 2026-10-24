"""Small local visual checks around detections, not a second card counter."""
import math
import cv2

from ..common import constants


class CardEvidence:
    def __init__(self):
        self.previous = []

    def observe(self, frame, predictions):
        current, result = [], []
        available = set(range(len(self.previous)))
        for pred in predictions:
            x, y = pred["cx"], pred["cy"]
            w, h = pred.get("width", 30), pred.get("height", 40)
            l, t = max(0, round(x-w/2)), max(0, round(y-h/2))
            r, b = min(frame.shape[1], round(x+w/2)), min(frame.shape[0], round(y+h/2))
            crop = frame[t:b, l:r]
            signature = (cv2.cvtColor(cv2.resize(crop, (24, 24)), cv2.COLOR_BGR2GRAY)
                         if crop.size else None)
            choices = [(math.hypot(x-self.previous[i][0], y-self.previous[i][1]), i)
                       for i in available]
            distance, index = min(choices, default=(float("inf"), None))
            unstable = signature is None
            if distance < max(w, h)*.75:
                available.remove(index)
                old = self.previous[index][2]
                unstable = bool(unstable or distance > max(3., min(w,h)*.15)
                                or (old is not None and signature is not None
                                    and cv2.absdiff(signature, old).mean() > 12.))
            current.append((x, y, signature))
            result.append(pred | {"unstable": bool(unstable and constants.CARD_EVIDENCE_ENABLED),
                "ambiguous": bool(constants.CARD_EVIDENCE_ENABLED and
                                  pred.get("class_margin", 1.) < .10)})
        self.previous = current
        return result
