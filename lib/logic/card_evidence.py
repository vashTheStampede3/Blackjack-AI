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
            signature = (cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.size else None)
            choices = [(math.hypot(x-self.previous[i][0], y-self.previous[i][1]), i)
                       for i in available]
            distance, index = min(choices, default=(float("inf"), None))
            unstable = signature is None
            if distance < max(w, h)*.75:
                available.remove(index)
                old = self.previous[index][2]
                unstable = bool(unstable or distance > max(3., min(w,h)*.15)
                                or (old is not None and signature is not None
                                    and self._changed((l, t, r, b, signature), old)))
            current.append((x, y, (l, t, r, b, signature) if signature is not None else None))
            result.append(pred | {"unstable": bool(unstable and constants.CARD_EVIDENCE_ENABLED),
                "ambiguous": bool(constants.CARD_EVIDENCE_ENABLED and
                                  pred.get("class_margin", 1.) < .10)})
        self.previous = current
        return result

    @staticmethod
    def _changed(current, old):
        """Compare identical screen pixels despite small detector-box jitter."""
        l, t, r, b, image = current
        ol, ot, oright, obottom, previous = old
        left, top, right, bottom = max(l, ol), max(t, ot), min(r, oright), min(b, obottom)
        shared = max(0, right-left) * max(0, bottom-top)
        if shared < .5 * min((r-l)*(b-t), (oright-ol)*(obottom-ot)):
            return True
        return bool(cv2.absdiff(
            image[top-t:bottom-t, left-l:right-l],
            previous[top-ot:bottom-ot, left-ol:right-ol]).mean() > 12.)
