"""One round ledger of physical-card candidates, using the existing CardCounter.

Rank/position matching cannot prove identity through arbitrary occlusion. When
the available observations admit a moved card or a new card, retain a doubt for
manual review rather than incrementing the shoe. No detector lives here.
"""
from dataclasses import asdict, dataclass, field
import math

from .cards import card_value


@dataclass
class RememberedCard:
    id: str
    rank: str | None
    cx: float
    cy: float
    width: float
    height: float
    first_seen: float
    last_seen: float
    owner: str
    observed_owner: str
    state: str = "pending"
    counted: bool = False
    hits: int = 0
    manual: bool = False
    reason: str | None = None
    possible_ids: list = field(default_factory=list)
    vx: float = 0.
    vy: float = 0.
    hand: int | None = 0
    initial_cx: float = 0.


class CardMemory:
    def __init__(self, counter, *, confirmations=2, empty_frames=5):
        self.counter = counter
        self.confirmations = confirmations
        self.required_empty = empty_frames
        self.cards = {}
        self.serial = 0
        self.round_number = 1
        self.empty_frames = 0
        self.empty_since = None
        self.had_cards = False
        self.split_roots = []
        self.now = 0.

    def _issue(self, card, reason):
        card.reason, card.state = reason, "doubtful"
        self.counter.mark_uncertainty(card.id, {"reason": reason, "rank": card.rank,
                                               "counted": card.counted, "round": self.round_number})

    def _new(self, pred, now, reason=None, possible_ids=()):
        if len(self.cards) >= 256:
            self.counter.mark_uncertainty("memory_capacity", {"reason": "Demasiadas cartas candidatas"})
            return None
        self.serial += 1
        key = f"{self.counter.shoe_id}:{self.round_number}:{self.serial}"
        card = RememberedCard(key, pred.get("name"), pred["cx"], pred["cy"],
            pred.get("width", 30), pred.get("height", 40), now, now,
            pred["owner"], pred["owner"], possible_ids=list(possible_ids), initial_cx=pred["cx"])
        self.cards[key] = card
        self.counter.mark_uncertainty(key, {"reason": reason or "Carta sin leer / pendiente",
                                           "rank": card.rank, "counted": False, "round": self.round_number})
        if reason:
            self._issue(card, reason)
        self._update(card, pred, now)
        return card

    def _update(self, card, pred, now):
        dt = now-card.last_seen
        if dt > 0:
            card.vx, card.vy = (pred["cx"]-card.cx)/dt, (pred["cy"]-card.cy)/dt
        card.cx, card.cy = pred["cx"], pred["cy"]
        card.width, card.height = pred.get("width", 30), pred.get("height", 40)
        card.last_seen, card.observed_owner = now, pred["owner"]
        if card.state in ("rejected", "retired"):
            return
        if card.manual:
            card.state = "confirmed"
            return
        if card.reason:
            return  # a known ambiguity is never silently turned into a clean count
        if pred.get("ambiguous") or pred.get("unstable") or pred.get("name") is None:
            # A previously confirmed card does not become a new unread card
            # because one observation is blurred. Keep its confirmed rank;
            # unconfirmed candidates still need consecutive clean sightings.
            if not card.counted:
                card.hits = 0
            return
        if card.counted and pred["name"] != card.rank:
            self._issue(card, "Rango cambiado; revisar carta")
            return
        if pred["name"] != card.rank:
            card.rank, card.hits = pred["name"], 0
        card.hits += 1
        if card.counted:
            card.state = "confirmed"
        elif card.hits >= self.confirmations:
            card.counted, card.state = True, "confirmed"
            self.counter.count_card(card.rank)
            self.counter.clear_uncertainty(card.id)

    @staticmethod
    def _distance(card, pred, now):
        dt = min(1., max(0., now-card.last_seen))
        return min(math.hypot(card.cx-pred["cx"], card.cy-pred["cy"]),
                   math.hypot(card.cx+card.vx*dt-pred["cx"], card.cy+card.vy*dt-pred["cy"]))

    def observe(self, predictions, now):
        self.now = now
        # One box per location per frame is one observation, never two votes.
        unique = []
        for pred in sorted(predictions, key=lambda p: p.get("confidence", 0), reverse=True):
            if pred.get("owner") not in ("mine", "dealer", "other"):
                continue
            if pred.get("class") in ("back", "cardback", "cuttingcard"):
                continue
            radius = max(3., min(pred.get("width", 30), pred.get("height", 40))*.35)
            if any(math.hypot(p["cx"]-pred["cx"], p["cy"]-pred["cy"]) < radius for p in unique):
                continue
            unique.append(pred)
        if unique:
            self.had_cards = True
            self.empty_frames, self.empty_since = 0, None
        else:
            self.empty_frames += 1
            if self.empty_since is None:
                self.empty_since = now

        choices, ambiguous = {}, {}
        original_ids = set(self.cards)
        same_rank_near = {
            key: any(
                pred.get("name") == card.rank
                and self._distance(card, pred, now) <= max(
                    card.width, card.height, pred.get("width", 30), pred.get("height", 40))*.85
                for pred in unique)
            for key, card in self.cards.items() if card.rank is not None and not card.manual
        }
        for index, pred in enumerate(unique):
            options = []
            for key, card in self.cards.items():
                distance = self._distance(card, pred, now)
                scale = max(card.width, card.height, pred.get("width", 30), pred.get("height", 40))
                same = card.rank == pred.get("name") or card.manual or card.rank is None
                if not same and same_rank_near.get(key, False):
                    # When the old rank is still visible nearby, a different
                    # overlapping rank can be a newly revealed card.
                    continue
                if distance <= scale*(.85 if same else .35):
                    options.append((distance + (0 if same else scale*.4), key))
            options.sort()
            margin = max(3., min(pred.get("width", 30), pred.get("height", 40))*.2)
            if len(options) > 1 and options[1][0]-options[0][0] <= margin:
                ambiguous[index] = [key for cost, key in options if cost-options[0][0] <= margin]
            else:
                choices[index] = options
        pairs = sorted((cost, index, key) for index, opts in choices.items() for cost, key in opts)
        matched, used = {}, set()
        for cost, index, key in pairs:
            if index not in matched and key not in used:
                matched[index] = key
                used.add(key)
        for index, key in matched.items():
            card, pred = self.cards[key], unique[index]
            shift = math.hypot(card.cx-pred["cx"], card.cy-pred["cy"])
            stable_same_rank = pred.get("name") == card.rank and card.rank is not None
            jitter = max(4., min(card.width, card.height,
                                 pred.get("width", 30), pred.get("height", 40))*.35)
            if (stable_same_rank and card.counted
                    and shift > jitter and not card.reason):
                # Once counted, a displaced same-rank box may be a moved card
                # or a second card after occlusion. A one-frame candidate is
                # still provisional: let it settle before assigning identity.
                reason = "Puede ser carta movida o nueva"
                self._issue(card, reason)
                self._new(pred, now, reason, [key])
            else:
                self._update(card, pred, now)
        for index, pred in enumerate(unique):
            if index in matched:
                continue
            possible = ambiguous.get(index, [])
            if not possible:
                possible = []
                for key in original_ids-used:
                    card = self.cards[key]
                    if card.rank != pred.get("name"):
                        continue
                    # A dealer HUD graphic cannot physically become a player
                    # card. Distant equal ranks on an eight-deck table are
                    # distinct unless their motion is spatially plausible.
                    if (card.owner == "dealer") != (pred["owner"] == "dealer"):
                        continue
                    scale = max(card.width, card.height,
                                pred.get("width", 30), pred.get("height", 40))
                    if self._distance(card, pred, now) <= scale*2.5:
                        possible.append(key)
            if possible:
                for key in possible:
                    self._issue(self.cards[key], "Puede ser carta movida o nueva")
                pending = next((c for c in self.cards.values() if not c.counted and c.reason
                                and math.hypot(c.cx-pred["cx"], c.cy-pred["cy"]) < max(4., min(c.width,c.height)*.35)), None)
                if pending:
                    self._update(pending, pred, now)
                else:
                    self._new(pred, now, "Puede ser carta movida o nueva", possible)
            else:
                self._new(pred, now)
        for key in original_ids-used:
            card = self.cards[key]
            if card.counted and not card.reason:
                card.state = "occluded"
        self._group_hands()

    @property
    def clear_ready(self):
        return (self.had_cards and self.empty_frames >= self.required_empty
                and self.empty_since is not None and self.now-self.empty_since >= 2.)

    @property
    def pending(self):
        return any((not c.counted or c.reason) and c.state not in ("rejected", "retired")
                   for c in self.cards.values())

    @property
    def advice_pending(self):
        return self.counter.count_uncertain or any(c.observed_owner != c.owner or c.hand is None
            for c in self.cards.values() if c.owner == "mine" or c.observed_owner == "mine")

    def _group_hands(self):
        own = sorted((c for c in self.cards.values() if c.owner == "mine" and c.counted),
                     key=lambda c: (c.first_seen, c.id))
        if not self.split_roots and len(own) >= 2:
            a, b = own[:2]
            if (a.rank == b.rank and abs(a.cx-b.cx) > max(abs(a.initial_cx-b.initial_cx)*1.8,
                                                        max(a.width,b.width)*2.5)):
                self.split_roots = [a.id, b.id]
        if len(self.split_roots) != 2:
            return
        roots = [self.cards[k] for k in self.split_roots]
        for card in own:
            if card.id in self.split_roots:
                card.hand = self.split_roots.index(card.id)
                continue
            distances = [math.hypot(card.cx-root.cx, card.cy-root.cy) for root in roots]
            card.hand = None if abs(distances[0]-distances[1]) < max(3.,card.width*.35) else distances.index(min(distances))

    def correct(self, identity, rank, *, expected_round=None):
        if rank is not None:
            card_value(rank)  # validate before retracting a previously counted card
        if not identity.startswith(self.counter.shoe_id+":"):
            raise ValueError("La carta pertenece a otro shoe")
        if expected_round is not None and expected_round != self.round_number:
            raise ValueError("La ronda cambió; revisá la corrección")
        card = self.cards.get(identity)
        if card is None:
            issue = self.counter.uncertainties.get(identity)
            if issue is None:
                raise ValueError("No existe esa carta pendiente")
            if issue.get("counted"):
                self.counter.uncount_card(issue["rank"])
            if rank is not None:
                self.counter.count_card(rank)
            self.counter.clear_uncertainty(identity)
            return
        if card.counted:
            self.counter.uncount_card(card.rank)
        if rank is None:
            card.counted, card.manual, card.rank = False, True, None
            card.state, card.reason = "rejected", None
        else:
            card.rank, card.manual, card.counted = rank, True, True
            card.state, card.reason, card.possible_ids = "confirmed", None, []
            self.counter.count_card(rank)
        self.counter.clear_uncertainty(identity)

    def detach_shoe(self):
        for card in self.cards.values():
            card.counted, card.manual = False, True
            card.state, card.reason = "retired", None

    def clear_round(self):
        # Unresolved issues already live in the shoe; clearing cannot erase them.
        self.cards.clear()
        self.split_roots.clear()
        self.empty_frames, self.empty_since, self.had_cards = 0, None, False
        self.round_number += 1

    def snapshot(self):
        return [asdict(card) for card in self.cards.values()]

    def get_state(self):
        return {"round_number": self.round_number, "serial": self.serial,
                "cards": self.snapshot(), "split_roots": list(self.split_roots)}

    def apply_state(self, state):
        restored = [RememberedCard(**row) for row in state.get("cards", [])]
        if any(not c.id.startswith(self.counter.shoe_id+":") and c.state != "retired" for c in restored):
            raise ValueError("La memoria guardada pertenece a otro shoe")
        self.cards = {c.id: c for c in restored}
        self.round_number = int(state["round_number"])
        self.serial = int(state.get("serial", 0))
        self.split_roots = [key for key in state.get("split_roots", []) if key in self.cards]
        self.had_cards = bool(self.cards)
