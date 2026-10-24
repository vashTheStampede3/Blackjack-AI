"""Read-only evidence for the compact overlay; never mutates shoe/cards."""
from collections import Counter
import time

from ..common.card_mappings import PLAYER_CLASS_MAP
from .cards import rank_of


def canonical(name):
    return {"A": "Ace", "J": "Jack", "Q": "Queen", "K": "King"}.get(
        rank_of(name), rank_of(name))


def compare_ranks(expected, observed):
    return Counter(map(canonical, expected)) == Counter(map(canonical, observed))


class RoundObservation:
    def __init__(self):
        self.captured_at = self.inferred_at = 0.0
        self.present = False
        self.empty = 0
        self.seats = {}
        self.dealer_ok = False
        self.count_uncertain = False

    def reset_round(self):
        self.present, self.empty = False, 3
        self.seats = {}
        self.dealer_ok = False
        self.inferred_at = 0.0

    def observe(self, engine, predictions, dealer_predictions):
        self.inferred_at = self.captured_at
        observed = {i: [] for i in range(len(engine.seats))}
        uncertain_seats = set()
        for pred in predictions:
            name = PLAYER_CLASS_MAP.get(pred["class"])
            if name:
                seat = engine._seat_for_point(pred["cx"], pred["cy"])
                if seat is not None:
                    observed[seat].append(name)
                    # Conservative display gate only; detector/count thresholds
                    # stay untouched. A model score is not calibrated accuracy.
                    if pred.get("confidence", 0) < .75:
                        uncertain_seats.add(seat)
        dealer = [PLAYER_CLASS_MAP[p["class"]] for p in dealer_predictions
                  if p["class"] in PLAYER_CLASS_MAP]
        any_cards = bool(dealer or any(observed.values()))
        self.empty = 0 if any_cards else self.empty + 1
        if any_cards:
            self.present = True
        elif self.empty >= 3:
            self.present = False
        self.seats = {}
        for seat in engine.seats:
            expected = [c["name"] for c in seat.cards]
            pending = any(k[0] == seat.index for k in engine._pending_extra)
            match = compare_ranks(expected, observed[seat.index])
            self.seats[seat.index] = bool(len(expected) >= 2 and match and not pending
                                          and seat.index not in uncertain_seats)
            if expected and seat.index in uncertain_seats:
                self.count_uncertain = True
            # Contradictory complete rank sets can already have affected count.
            if expected and observed[seat.index] and len(expected) == len(observed[seat.index]) and not match:
                self.count_uncertain = True
        expected_dealer = ([engine.dealer_card] if engine.dealer_card else []) + [
            p["rank"] for p in engine.dealer_extras]
        self.dealer_ok = bool(engine.dealer_locked and expected_dealer
                              and compare_ranks(expected_dealer, dealer)
                              and not engine._pending_dealer
                              and all(p.get("confidence", 0) >= .75 for p in dealer_predictions
                                      if p["class"] in PLAYER_CLASS_MAP))
        if expected_dealer and any(p.get("confidence", 0) < .75 for p in dealer_predictions
                                    if p["class"] in PLAYER_CLASS_MAP):
            self.count_uncertain = True
        if expected_dealer and dealer and len(expected_dealer) == len(dealer) and not compare_ranks(expected_dealer, dealer):
            self.count_uncertain = True

    def snapshot(self):
        return {"captured_at": self.captured_at, "inferred_at": self.inferred_at,
                "present": self.present, "empty": self.empty,
                "seats": dict(self.seats), "dealer_ok": self.dealer_ok,
                "count_uncertain": self.count_uncertain}


ACTIONS = {"H": "Hit", "S": "Stand", "D": "Double", "P": "Split", "R": "Surrender",
           "D/H": "Double / Hit", "D/S": "Double / Stand", "R/H": "Surrender / Hit"}


def overlay_text(snap, running, now=None):
    """Never selects a seat or fabricates an action when evidence is missing."""
    now = time.time() if now is None else now
    snap = snap or {}
    q = snap.get("reading") or {}
    count = snap.get("count") or {}
    marker = " ?" if q.get("count_uncertain") else ""
    count_line = f"RC {count.get('running', 0):+d}   TC {count.get('true', 0):+.1f}{marker}"
    if not running:
        return "warning", count_line, "Detección detenida"
    if snap.get("error"):
        return "warning", count_line, "Error de captura / detección"
    if not q.get("captured_at") or now-q["captured_at"] > 3:
        return "warning", count_line, "Sin actualización"
    if not q.get("present"):
        return "idle", count_line, "Esperando ronda"
    mine = [s for s in snap.get("seats", []) if s.get("mine")]
    if not mine:
        return "warning", count_line, "Seleccioná tu asiento"
    if now-q.get("inferred_at", 0) > 3 or not q.get("dealer_ok"):
        return "warning", count_line, "Lectura incompleta"
    lines = []
    for seat in mine:
        prefix = f"P{seat['index']+1}: "
        if not q.get("seats", {}).get(seat["index"]):
            lines.append(prefix+"Lectura incompleta")
            continue
        # Count-dependent optimal advice is unsafe after a known count conflict.
        action = (seat.get("book_action") if q.get("count_uncertain") else
                  seat.get("optimal_action") or seat.get("book_action"))
        if seat.get("split"):
            codes = action if isinstance(action, list) else []
            text = " · ".join(f"H{i+1} {ACTIONS.get(code, 'Sin consejo')}"
                              for i, code in enumerate(codes))
        else:
            text = ACTIONS.get(action, "Sin consejo") if isinstance(action, str) else "Sin consejo"
        lines.append(prefix+(text or "Lectura incompleta"))
    if q.get("count_uncertain"):
        lines.append("Conteo dudoso · consejo básico")
    return "active", count_line, "\n".join(lines)
