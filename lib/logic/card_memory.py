"""One round ledger of physical-card candidates, using the existing CardCounter.

Rank/position matching cannot prove identity through arbitrary occlusion. When
the available observations admit a moved card or a new card, retain a doubt for
manual review rather than incrementing the shoe. No detector lives here.
"""
from dataclasses import asdict, dataclass, field
from copy import deepcopy
import math

from ..common import constants
from .card_layout import TableGroupTracker


@dataclass(frozen=True)
class CorrectionResult:
    ok: bool
    reason: str
    rc_delta: int = 0


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
    round_id: int = 1
    suit: str | None = None
    counted_rank: str | None = None
    counted_name: str | None = None
    trajectory: list = field(default_factory=list)
    seat_id: str | None = None
    manual_seat_override: str | None = None
    ambiguous_seat: bool = False
    status: str = "active"

    @property
    def card_id(self):
        return self.id

    @property
    def bbox(self):
        return (self.cx-self.width/2, self.cy-self.height/2,
                self.cx+self.width/2, self.cy+self.height/2)

    @property
    def center(self):
        return (self.cx, self.cy)

    @property
    def first_seen_ts(self):
        return self.first_seen

    @property
    def last_seen_ts(self):
        return self.last_seen

    @property
    def counted_in_shoe(self):
        return self.counted

    @property
    def velocity(self):
        return (self.vx, self.vy)

    @property
    def char_scale(self):
        return math.sqrt(max(1., self.width) * max(1., self.height))

    @property
    def is_rotated_double(self):
        return self.width / max(1., self.height) > 1.15


TrackedCard = RememberedCard


class CardMemory:
    def __init__(self, counter, *, confirmations=2, empty_frames=5, serial=0):
        self.counter = counter
        self.confirmations = confirmations
        self.required_empty = empty_frames
        self.cards = {}
        # Engine replacements hand off the high-water mark, including IDs of
        # cards no longer in this round. Persist it even for an empty ledger.
        self.serial = max(serial, self._identity_serials(getattr(counter, "uncertainties", {})))
        self.round_number = 1
        self.empty_frames = 0
        self.empty_since = None
        self.had_cards = False
        self.split_roots = []
        self.now = 0.
        self.group_tracker = TableGroupTracker()

    def _identity_serials(self, identities):
        high = 0
        for identity in identities:
            if isinstance(identity, str):
                parts = identity.rsplit(":", 2)
                if (len(parts) == 3 and parts[0] == self.counter.shoe_id
                        and parts[1].isdigit() and parts[2].isdigit()):
                    high = max(high, int(parts[2]))
        return high

    def _issue(self, card, reason):
        card.reason, card.state = reason, "doubtful"
        self.counter.mark_uncertainty(card.id, {"reason": reason, "rank": card.rank,
                                               "counted": card.counted, "round": self.round_number})

    @staticmethod
    def _normalize(pred):
        pred = dict(pred)
        name = pred.get("name")
        if isinstance(name, str) and " of " in name:
            rank, _of, suit = name.partition(" of ")
            pred["name"] = rank
            pred.setdefault("suit", {"Spades": "S", "Hearts": "H",
                                     "Diamonds": "D", "Clubs": "C"}.get(suit))
        return pred

    @staticmethod
    def _shoe_name(card):
        return CardMemory._counted_name(card.rank, card.suit)

    @staticmethod
    def _counted_name(rank, suit):
        suit_name = {"S": "Spades", "H": "Hearts", "D": "Diamonds", "C": "Clubs"}.get(suit)
        return f"{rank} of {suit_name}" if suit_name else rank

    def _new(self, pred, now, reason=None, possible_ids=()):
        if len(self.cards) >= 256:
            self.counter.mark_uncertainty("memory_capacity", {"reason": "Demasiadas cartas candidatas"})
            return None
        self.serial += 1
        key = f"{self.counter.shoe_id}:{self.round_number}:{self.serial}"
        pred = self._normalize(pred)
        card = RememberedCard(key, pred.get("name"), pred["cx"], pred["cy"],
            pred.get("width", 30), pred.get("height", 40), now, now,
            pred["owner"], pred["owner"], possible_ids=list(possible_ids), initial_cx=pred["cx"],
            round_id=self.round_number, suit=pred.get("suit"))
        self.cards[key] = card
        self.counter.mark_uncertainty(key, {"reason": reason or "Carta sin leer / pendiente",
                                           "rank": card.rank, "counted": False, "round": self.round_number})
        if reason:
            self._issue(card, reason)
        self._update(card, pred, now)
        return card

    def _update(self, card, pred, now):
        pred = self._normalize(pred)
        dt = now-card.last_seen
        if dt > 0:
            card.vx, card.vy = (pred["cx"]-card.cx)/dt, (pred["cy"]-card.cy)/dt
        card.cx, card.cy = pred["cx"], pred["cy"]
        card.width, card.height = pred.get("width", 30), pred.get("height", 40)
        card.last_seen, card.observed_owner = now, pred["owner"]
        card.trajectory.append((now, card.cx, card.cy))
        if len(card.trajectory) > 12:
            del card.trajectory[:-12]
        if card.suit is None and pred.get("suit"):
            card.suit = pred["suit"]
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
            card.counted_rank = card.rank
            card.counted_name = self._shoe_name(card)
            self.counter.count_card(card.counted_name)
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
        for raw_pred in sorted(predictions, key=lambda p: p.get("confidence", 0), reverse=True):
            pred = self._normalize(raw_pred)
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
        return self.counter.count_uncertain or any(
            c.observed_owner != c.owner or c.hand is None or c.ambiguous_seat
            for c in self.cards.values()
            if c.state not in ("rejected", "retired")
            and (c.owner == "mine" or c.observed_owner == "mine" or c.ambiguous_seat))

    def _group_hands(self):
        self.group_tracker.update_groups(list(self.cards.values()))
        own = sorted((c for c in self.cards.values() if c.owner == "mine" and c.counted),
                     key=lambda c: (c.first_seen, c.id))
        if self.split_roots and not set(self.split_roots) <= {c.id for c in own}:
            self.split_roots.clear()
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

    @staticmethod
    def _correction_values(rank, suit):
        if not isinstance(rank, str):
            raise ValueError("invalid_rank")
        bare, separator, named_suit = rank.partition(" of ")
        bare = {"A": "Ace", "J": "Jack", "Q": "Queen", "K": "King"}.get(bare, bare)
        if bare not in constants.CARD_RANKS:
            raise ValueError("invalid_rank")
        suits = {"S": "S", "H": "H", "D": "D", "C": "C",
                 "Spades": "S", "Hearts": "H", "Diamonds": "D", "Clubs": "C",
                 "♠": "S", "♥": "H", "♦": "D", "♣": "C"}
        if suit is not None and (not isinstance(suit, str) or suit not in suits):
            raise ValueError("invalid_suit")
        code = suits[suit] if suit is not None else None
        if separator:
            if named_suit not in constants.CARD_SUITS:
                raise ValueError("invalid_suit")
            if code is not None and code != suits[named_suit]:
                raise ValueError("invalid_suit")
            code = suits[named_suit]
        return bare, code

    def correct(self, identity, rank, *, expected_round=None):
        """Compatibility entry point with the same active-identity safeguards."""
        result = self.apply_correction(
            identity, self.round_number if expected_round is None else expected_round,
            "not_a_card" if rank is None else "set_rank", new_rank=rank)
        if not result.ok:
            reason = ("La ronda cambió; revisá la corrección" if result.reason == "round_changed"
                      else "La carta pertenece a otro shoe" if result.reason == "shoe_changed"
                      else result.reason)
            raise ValueError(reason)
        return result

    def apply_correction(self, identity, expected_round_id, action, *,
                         new_rank=None, new_suit=None, new_seat_id=None):
        """Validate first. The owning engine serializes all ledger mutations."""
        if type(expected_round_id) is not int or expected_round_id != self.round_number:
            return CorrectionResult(False, "round_changed")
        if not isinstance(identity, str):
            return CorrectionResult(False, "invalid_identity")
        if not identity.startswith(self.counter.shoe_id + ":"):
            return CorrectionResult(False, "shoe_changed")
        card = self.cards.get(identity)
        if card is None or card.round_id != self.round_number or card.id != identity:
            return CorrectionResult(False, "card_not_found")
        if card.state == "rejected" or card.status == "not_a_card":
            return CorrectionResult(False, "already_discarded")
        if card.state == "retired" or card.status != "active":
            return CorrectionResult(False, "card_retired")
        if action == "change_seat":
            if not isinstance(new_seat_id, str) or new_seat_id not in {f"J{i}" for i in range(1, 8)}:
                return CorrectionResult(False, "invalid_seat")
            if new_rank is not None or new_suit is not None:
                return CorrectionResult(False, "invalid_action")
            if card.manual_seat_override == new_seat_id and card.seat_id == new_seat_id and not card.ambiguous_seat:
                return CorrectionResult(False, "unchanged")
            card.manual_seat_override = card.seat_id = new_seat_id
            card.ambiguous_seat = False
            self._group_hands()
            return CorrectionResult(True, "seat_changed")
        if action == "not_a_card":
            if any(value is not None for value in (new_rank, new_suit, new_seat_id)):
                return CorrectionResult(False, "invalid_action")
            before = self.counter.running_count
            if card.counted:
                self.counter.uncount_card(card.counted_name or card.counted_rank or card.rank)
            card.counted, card.counted_rank, card.counted_name = False, None, None
            card.manual, card.rank = True, None
            card.state, card.status, card.reason = "rejected", "not_a_card", None
            card.possible_ids = []
            self.counter.clear_uncertainty(identity)
            self._group_hands()
            return CorrectionResult(True, "discarded", self.counter.running_count - before)
        if action != "set_rank" or new_rank is None or new_seat_id is not None:
            return CorrectionResult(False, "invalid_action")
        try:
            rank, suit = self._correction_values(new_rank, new_suit)
        except ValueError as exc:
            return CorrectionResult(False, str(exc))
        suit = card.suit if suit is None else suit
        name = self._counted_name(rank, suit)
        if (card.counted and card.rank == rank and card.suit == suit
                and card.counted_name == name and card.manual and card.state == "confirmed"
                and not card.reason and not card.possible_ids
                and identity not in self.counter.uncertainties):
            return CorrectionResult(False, "unchanged")
        before = self.counter.running_count
        old_name = (card.counted_name or card.counted_rank or card.rank) if card.counted else None
        # Even a same-rank manual confirmation must update suit accounting if
        # that contribution was previously recorded without (or with another) suit.
        if old_name != name:
            if old_name is not None:
                self.counter.uncount_card(old_name)
            self.counter.count_card(name)
        card.rank, card.suit = rank, suit
        card.counted_rank, card.counted_name = rank, name
        card.counted, card.manual = True, True
        card.state, card.reason, card.possible_ids = "confirmed", None, []
        self.counter.clear_uncertainty(identity)
        self._group_hands()
        return CorrectionResult(True, "rank_updated", self.counter.running_count - before)

    def detach_shoe(self):
        for card in self.cards.values():
            card.counted, card.manual = False, True
            card.state, card.reason = "retired", None
            card.status = "retired"
        self.split_roots.clear()
        self._group_hands()

    def clear_round(self):
        # Unresolved issues already live in the shoe; clearing cannot erase them.
        self.cards.clear()
        self.split_roots.clear()
        self.empty_frames, self.empty_since, self.had_cards = 0, None, False
        self.round_number += 1
        self.group_tracker.reset_for_round()

    def snapshot(self):
        return [asdict(card) for card in self.cards.values()]

    def get_state(self):
        return {"round_number": self.round_number, "serial": self.serial,
                "cards": self.snapshot(), "split_roots": list(self.split_roots)}

    def apply_state(self, state):
        allowed = RememberedCard.__dataclass_fields__
        restored = [RememberedCard(**{k: deepcopy(v) for k, v in row.items() if k in allowed})
                    for row in state.get("cards", [])]
        if any(not isinstance(c.id, str) for c in restored):
            raise ValueError("Identidad guardada inválida")
        if any(not c.id.startswith(self.counter.shoe_id+":") and c.state != "retired" for c in restored):
            raise ValueError("La memoria guardada pertenece a otro shoe")
        round_number, serial = int(state["round_number"]), int(state.get("serial", 0))
        if round_number < 1 or serial < 0 or len({c.id for c in restored}) != len(restored):
            raise ValueError("Identidades o secuencia guardadas inválidas")
        for card in restored:
            parts = card.id.rsplit(":", 2)
            if card.state != "retired" and (
                    len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit()
                    or int(parts[1]) != round_number or card.round_id != round_number
                    or int(parts[2]) < 1):
                raise ValueError("La carta guardada no pertenece a la ronda activa")
        serial = max(self.serial, serial, self._identity_serials(c.id for c in restored),
                     self._identity_serials(self.counter.uncertainties))
        self.cards = {c.id: c for c in restored}
        self.round_number = round_number
        self.serial = serial
        self.split_roots = [key for key in state.get("split_roots", []) if key in self.cards]
        self.had_cards = bool(self.cards)
        self.group_tracker = TableGroupTracker()
        self._group_hands()
