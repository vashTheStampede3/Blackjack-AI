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
        if not _inside(zones["mine"], zones["table"]):
            raise ValueError("Mi asiento debe quedar completamente dentro de Mesa.")
        # The dealer's selected representation may be a separate casino card HUD.
        # Keep that region independent; never stretch Mesa across the intervening UI.
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
        if _contains(self.dealer, (x, y)):
            return "dealer"
        if not _contains(self.table, (x, y)):
            return None
        if _contains(self.mine, (x, y)):
            return "mine"
        return "other"


@dataclass
class PlayerSeatGroup:
    """A presentation/advice group; it never owns shoe-count decisions."""
    seat_id: str
    anchor_x: float
    anchor_y: float
    avg_scale: float
    cards: list = None
    is_hero: bool = False
    is_ambiguous: bool = False
    has_split: bool = False
    closed_by_double: bool = False
    first_seen: float = 0.
    last_seen: float = 0.

    def __post_init__(self):
        if self.cards is None:
            self.cards = []

    @property
    def display_label(self):
        label = f"{self.seat_id} (Vos)" if self.is_hero else self.seat_id
        return f"{label} · asiento dudoso" if self.is_ambiguous else label


class TableGroupTracker:
    """Group table cards by position while keeping assignments fixed per round.

    A close call is exposed as ambiguous. Groups are display labels, not a
    source of card identity or count events.
    """
    def __init__(self, max_seats=7, cluster_radius_scales=2.1,
                 ambiguity_margin=0.28):
        self.max_seats = max_seats
        self.cluster_radius_scales = cluster_radius_scales
        self.ambiguity_margin = ambiguity_margin
        self._groups = {}
        self._hero_seat_id = None

    @staticmethod
    def _scale(card):
        return max(1., math.sqrt(max(1., card.width) * max(1., card.height)))

    def reset_for_round(self, hero_seat_id=None):
        self._groups.clear()
        self._hero_seat_id = hero_seat_id

    def update_groups(self, cards):
        active = [c for c in cards if c.state not in ("rejected", "retired")
                  and c.owner != "dealer"]
        self._now = max((c.last_seen for c in active), default=0.)
        for group in self._groups.values():
            group.cards = []
            group.is_ambiguous = False

        pending = []
        for card in sorted(active, key=lambda c: (c.first_seen, -c.cx)):
            target = getattr(card, "manual_seat_override", None)
            if target and target not in self._groups:
                self._groups[target] = PlayerSeatGroup(target, card.cx, card.cy,
                                                       self._scale(card))
            target = target or card.seat_id
            if target in self._groups:
                self._put(self._groups[target], card)
            else:
                pending.append(card)

        # First sightings in the same frame seed groups right-to-left.
        for card in pending:
            if card.observed_owner == "mine" and not card.manual_seat_override:
                hero = next((group for group in self._groups.values()
                             if any(existing.observed_owner == "mine"
                                    for existing in group.cards)), None)
                if hero is not None:
                    self._put(hero, card)
                    continue
                if len(self._groups) < self.max_seats:
                    group = PlayerSeatGroup(f"TMP_{len(self._groups)+1}",
                                            card.cx, card.cy, self._scale(card))
                    self._groups[group.seat_id] = group
                    self._put(group, card)
                    continue
            scored = sorted(((self._cost(card, group), group)
                             for group in self._groups.values()),
                            key=lambda pair: pair[0])
            if not scored or scored[0][0] > self.cluster_radius_scales:
                if len(self._groups) >= self.max_seats:
                    card.ambiguous_seat = True
                    continue
                group = PlayerSeatGroup(f"TMP_{len(self._groups)+1}", card.cx,
                                        card.cy, self._scale(card))
                self._groups[group.seat_id] = group
                self._put(group, card)
                continue
            best_cost, group = scored[0]
            ambiguous = (len(scored) > 1 and
                         scored[1][0] - best_cost < self.ambiguity_margin)
            card.ambiguous_seat = ambiguous
            if ambiguous:
                group.is_ambiguous = True
                self._groups[scored[1][1].seat_id].is_ambiguous = True
            self._put(group, card)

        # Give temporary groups stable right-to-left labels once per creation.
        temporary = sorted((g for g in self._groups.values()
                            if g.seat_id.startswith("TMP_")),
                           key=lambda g: -g.anchor_x)
        for group in temporary:
            index = 1
            while f"J{index}" in self._groups:
                index += 1
            old = group.seat_id
            group.seat_id = f"J{index}"
            self._groups.pop(old)
            self._groups[group.seat_id] = group
            for card in group.cards:
                card.seat_id = group.seat_id
        for group in self._groups.values():
            group.is_hero = group.seat_id == self._hero_seat_id or any(
                card.observed_owner == "mine" for card in group.cards)
            if len(group.cards) >= 2:
                xs = [card.cx for card in group.cards]
                group.has_split = max(xs) - min(xs) > 1.45 * group.avg_scale
                newest = max(group.cards, key=lambda card: card.first_seen)
                group.closed_by_double = (len(group.cards) >= 3
                                          and newest.is_rotated_double)
            for card in group.cards:
                card.ambiguous_seat = bool(card.ambiguous_seat or group.is_ambiguous)
        ordered = sorted(self._groups.values(), key=lambda group: -group.anchor_x)
        for index, first in enumerate(ordered):
            for second in ordered[index+1:]:
                scale = (first.avg_scale + second.avg_scale) / 2
                if math.hypot(first.anchor_x-second.anchor_x,
                              first.anchor_y-second.anchor_y) < .85*scale:
                    first.is_ambiguous = second.is_ambiguous = True
                    for card in first.cards + second.cards:
                        card.ambiguous_seat = True
        return sorted(self._groups.values(), key=lambda group: -group.anchor_x)

    def _put(self, group, card):
        group.cards.append(card)
        card.seat_id = group.seat_id
        card.ambiguous_seat = bool(getattr(card, "ambiguous_seat", False))
        group.is_ambiguous = group.is_ambiguous or card.ambiguous_seat
        n = len(group.cards)
        group.anchor_x += (card.cx - group.anchor_x) / n
        group.anchor_y += (card.cy - group.anchor_y) / n
        group.avg_scale += (self._scale(card) - group.avg_scale) / n
        group.first_seen = (card.first_seen if n == 1 else
                            min(group.first_seen, card.first_seen))
        group.last_seen = max(group.last_seen, card.last_seen)

    def _cost(self, card, group):
        vx, vy = card.vx, card.vy
        px, py = card.cx + vx * .08, card.cy + vy * .08
        scale = max(12., (self._scale(card) + group.avg_scale) / 2)
        distance = math.hypot((px-group.anchor_x)/scale, .85*(py-group.anchor_y)/scale)
        scale_cost = .35 * abs(self._scale(card)-group.avg_scale)/scale
        temporal_cost = .10 * min(1., abs(card.first_seen-group.last_seen)/.75)
        return distance + scale_cost + temporal_cost

    def set_hero_seat(self, seat_id):
        self._hero_seat_id = seat_id
        for group in self._groups.values():
            group.is_hero = group.seat_id == seat_id

    def ordered_groups(self):
        return sorted(self._groups.values(), key=lambda group: -group.anchor_x)
