"""Validated native-pixel zones, independent of detectors and card memory."""
from copy import deepcopy
from dataclasses import dataclass
import math

SEVEN_SEATS = "seven_seats"
TABLE = "table"
SINGLE_SEAT = "single_seat"
MODE_LABELS = {SEVEN_SEATS: "7 asientos", TABLE: "Mesa + Mi asiento + Crupier",
               SINGLE_SEAT: "Un asiento + Crupier"}
ZONE_LABELS = {"table": "Mesa", "mine": "Mi asiento", "dealer": "Crupier"}
ZONE_COLORS = {"table": "#35c9b5", "mine": "#ff9f1a", "dealer": "#ff4d4d"}
_EPS = 1e-8


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def _edges(poly):
    return zip(poly, poly[1:] + poly[:1])


def _intersections(a, b, c, d):
    """Parameters on segment a-b intersecting c-d, including boundary contact."""
    ab, cd, ac = _sub(b, a), _sub(d, c), _sub(c, a)
    denominator = _cross(ab, cd)
    if abs(denominator) > _EPS:
        t, u = _cross(ac, cd) / denominator, _cross(ac, ab) / denominator
        return [max(0., min(1., t))] if -_EPS <= t <= 1 + _EPS and -_EPS <= u <= 1 + _EPS else []
    if abs(_cross(ac, ab)) > _EPS:
        return []
    length2 = ab[0] ** 2 + ab[1] ** 2
    if length2 <= _EPS:
        return []
    params = [sum(v * w for v, w in zip(_sub(p, a), ab)) / length2 for p in (c, d)]
    left, right = max(0., min(params)), min(1., max(params))
    return [left, right] if left <= right + _EPS else []


def _contains(poly, point):
    x, y = point
    inside = False
    for a, b in _edges(poly):
        if (abs(_cross(_sub(b, a), _sub(point, a))) <= _EPS
                and min(a[0], b[0]) - _EPS <= x <= max(a[0], b[0]) + _EPS
                and min(a[1], b[1]) - _EPS <= y <= max(a[1], b[1]) + _EPS):
            return True
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def _polygon(points, label, resolution):
    try:
        if any(len(p) != 2 for p in points):
            raise ValueError
        poly = tuple((float(x), float(y)) for x, y in points)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: usá puntos con dos coordenadas finitas.") from None
    if len(poly) > 1 and poly[0] == poly[-1]:
        poly = poly[:-1]
    if len(poly) < 3:
        raise ValueError(f"Dibujá {label} con al menos 3 puntos.")
    if not all(math.isfinite(v) for p in poly for v in p):
        raise ValueError(f"{label}: las coordenadas deben ser finitas.")
    w, h = resolution
    if any(not (0 <= x <= w and 0 <= y <= h) for x, y in poly):
        raise ValueError(f"{label} sale del fotograma {w}×{h}.")
    edges = list(_edges(poly))
    if any(a == b for a, b in edges):
        raise ValueError(f"{label}: hay puntos repetidos juntos.")
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            if _intersections(a, b, *edges[j]):
                raise ValueError(f"{label}: el borde se cruza; ajustá los puntos.")
    if abs(sum(_cross(a, b) for a, b in edges)) <= _EPS:
        raise ValueError(f"{label} no tiene área.")
    return poly


def _inside(child, parent):
    if not all(_contains(parent, p) for p in child):
        return False
    # Vertices alone are insufficient for a concave table: an edge can leave it.
    for a, b in _edges(child):
        cuts = sorted({0., 1., *(t for c, d in _edges(parent) for t in _intersections(a, b, c, d))})
        for lo, hi in zip(cuts, cuts[1:]):
            t = (lo + hi) / 2
            if not _contains(parent, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))):
                return False
    return True


def _overlaps(first, second):
    return (any(_intersections(a, b, c, d) for a, b in _edges(first) for c, d in _edges(second))
            or _contains(first, second[0]) or _contains(second, first[0]))


@dataclass(frozen=True)
class CardLayout:
    mode: str
    resolution: tuple
    table: tuple
    mine: tuple
    dealer: tuple
    reference_source: dict | None = None

    @classmethod
    def from_payload(cls, data, resolution):
        if not isinstance(data, dict) or data.get("mode") not in (TABLE, SINGLE_SEAT):
            raise ValueError("El modo de zonas no es válido.")
        if (len(resolution) != 2 or any(not isinstance(v, int) or v <= 1 for v in resolution)
                or data.get("resolution") != list(resolution)):
            raise ValueError("La resolución del perfil no coincide; no se adapta automáticamente.")
        zones = {key: _polygon(data.get(key, []), label, resolution) for key, label in ZONE_LABELS.items()}
        for key in ("mine", "dealer"):
            if not _inside(zones[key], zones["table"]):
                raise ValueError(f"{ZONE_LABELS[key]} debe quedar completamente dentro de Mesa.")
        if _overlaps(zones["mine"], zones["dealer"]):
            raise ValueError("Mi asiento y Crupier no pueden tocarse ni superponerse.")
        return cls(data["mode"], tuple(resolution), **zones,
                   reference_source=deepcopy(data.get("reference_source")))

    def to_payload(self):
        data = {"mode": self.mode, "resolution": list(self.resolution),
                **{key: [list(p) for p in getattr(self, key)] for key in ZONE_LABELS}}
        if self.reference_source is not None:
            data["reference_source"] = deepcopy(self.reference_source)
        return data

    def owner_at(self, x, y):
        """Tag a detection already expressed in native frame pixels; never count."""
        if not _contains(self.table, (x, y)):
            return None
        if _contains(self.mine, (x, y)):
            return "mine"
        return "dealer" if _contains(self.dealer, (x, y)) else "other"
