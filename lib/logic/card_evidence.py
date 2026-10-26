"""Small local visual checks around detections, not a second card counter."""
import math
import cv2

from ..common import constants
from ..common.card_mappings import PLAYER_CLASS_MAP


def _rank_suit(prediction):
    name = prediction.get("name") or PLAYER_CLASS_MAP.get(
        prediction.get("class"), prediction.get("class"))
    rank, _, suit = str(name).partition(" of ")
    return rank, prediction.get("suit") or suit or None


def resolve_inverted_corners(predictions):
    """Reconcile opposite 6/9 indices, never nearby staggered upper indices.

    This is a conservative geometric check for index-sized boxes, not an
    orientation classifier. Two different physical cards can be indistinguishable
    without pixels of their upper indices; isolated moderate 6/9 stays uncertain.
    Pairing needs compatible ownership/suit and a long lower-right diagonal.
    """
    observations = [dict(p) for p in predictions]
    options = []
    for i, upper in enumerate(observations):
        rank, suit = _rank_suit(upper)
        if rank not in ("6", "9"):
            continue
        w, h = upper.get("width", 30), upper.get("height", 40)
        for j, lower in enumerate(observations):
            other, other_suit = _rank_suit(lower)
            if {rank, other} != {"6", "9"} or upper.get("owner") != lower.get("owner"):
                continue
            if suit and other_suit and suit != other_suit:
                continue
            lw, lh = lower.get("width", 30), lower.get("height", 40)
            dx, dy = lower["cx"]-upper["cx"], lower["cy"]-upper["cy"]
            if (min(w, lw, h, lh) <= 0
                    or not .5 <= w/lw <= 2 or not .5 <= h/lh <= 2):
                continue
            if (.7*max(w, lw) <= dx <= 4*max(w, lw)
                    and 1.4*max(h, lh) <= dy <= 6*max(h, lh)
                    and .8 <= dy/dx <= 4.5):
                options.append((i, j))
    # Multiple plausible counterparts cannot establish a physical pair.
    paired, removed = set(), set()
    for i, j in options:
        if (sum(i in pair for pair in options) != 1
                or sum(j in pair for pair in options) != 1):
            continue
        observations[i]["inverted_corner_pair"] = True
        observations[i]["corner_observations"] = [dict(observations[i]), dict(observations[j])]
        paired.add(i)
        removed.add(j)
    result = []
    for i, pred in enumerate(observations):
        if i in removed:
            continue
        rank, _ = _rank_suit(pred)
        if rank in ("6", "9") and i not in paired and not pred.get("inverted_corner_pair"):
            if pred.get("confidence", 1.) < .70:
                pred["ambiguous"] = True
        result.append(pred)
    return result


class CardEvidence:
    def __init__(self):
        self.previous = []

    def observe(self, frame, predictions):
        current, result = [], []
        available = set(range(len(self.previous)))
        for pred in resolve_inverted_corners(predictions):
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
            result.append(pred | {
                "bbox": (x-w/2, y-h/2, x+w/2, y+h/2),
                "char_scale": math.sqrt(max(1., w) * max(1., h)),
                "is_rotated_double": w / max(1., h) > 1.15,
                "unstable": bool(unstable and constants.CARD_EVIDENCE_ENABLED),
                "ambiguous": bool(pred.get("ambiguous") or (
                    constants.CARD_EVIDENCE_ENABLED and pred.get("class_margin", 1.) < .10))})
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
