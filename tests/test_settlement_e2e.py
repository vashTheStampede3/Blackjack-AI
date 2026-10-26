"""Settlement end-to-end (Test-E2E): a scripted multi-round shoe of synthetic
per-frame model predictions driven through the REAL ingest path
(_process_dealer / _process_players / _maybe_auto_new_round), respecting every
confirmation gate:

  * initial cards confirm across distinct agreeing frames before counting
  * hits confirm across EXTRA_CARD_CONFIRM_CYCLES consecutive cycles
  * the dealer up-card confirms across DEALER_CONFIRM_FRAMES agreeing frames
  * dealer playout draws confirm like hits
  * the auto round reset needs EMPTY_FRAMES_FOR_RESET consecutive empty
    player frames AND the dealer-area-empty recheck over the same window

Three rounds are replayed on one continuous shoe: (a) a plain win + lose
two-seat round, (b) a player natural paying 3:2, (c) a dealer bust round
whose reset is first held by a lingering dealer-area card. Seat A is marked
as mine with a bet placed so the session P&L books; outcomes are asserted
from the live engine state and from the persisted rounds in the store.

Run:  .venv\\Scripts\\python -m pytest tests\\test_settlement_e2e.py -q
"""

import json
import shutil
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

from lib.common import constants
from lib.common.card_mappings import DEALER_CLASS_MAP, PLAYER_CLASS_MAP
from lib.logic.engine import DetectionEngine
from lib.logic.session_store import SessionStore

# Full card name -> player-model class ("10 of Hearts" -> "a10"); keep the
# canonical class when several classes map to one card (the "b87" stray).
NAME_TO_CLASS = {}
for _cls, _name in PLAYER_CLASS_MAP.items():
    NAME_TO_CLASS.setdefault(_name, _cls)
RANK_TO_DEALER_CLASS = {rank: cls for cls, rank in DEALER_CLASS_MAP.items()}

# Dealer-crop coordinates for the up-card and the playout draws, spaced
# beyond SAME_CARD_DISTANCE_PX so position dedupe can never merge them.
DEALER_UP_XY = (480.0, 300.0)
DEALER_DRAW_XY = [(640.0, 300.0), (800.0, 300.0)]


def player_pred(name, point, confidence):
    """One synthetic player-model prediction (normalized models.py shape)."""
    return {"cx": float(point[0]), "cy": float(point[1]), "width": 60.0,
            "height": 85.0, "class": NAME_TO_CLASS[name],
            "confidence": confidence}


def dealer_pred(rank, point=DEALER_UP_XY, confidence=0.9):
    """One synthetic dealer-model prediction (bare-rank classes)."""
    return {"cx": float(point[0]), "cy": float(point[1]), "width": 60.0,
            "height": 85.0, "class": RANK_TO_DEALER_CLASS[rank],
            "confidence": confidence}


class SettlementE2E(unittest.TestCase):
    A, B = 0, 1          # seat A is mine, seat B is another player
    BET = 50.0

    def setUp(self):
        # Test-environment config: no exact pre-deal sweeps (~15 s each), no
        # side-bet EV jobs, no bankroll/settings.json writes on settle.
        # Settlement, the round lifecycle and P&L depend on none of these.
        self._betting = {k: constants.BETTING[k]
                         for k in ("use_exact_edge", "auto_bankroll")}
        self._sidebets = {k: cfg["enabled"]
                          for k, cfg in constants.SIDE_BETS.items()}
        constants.BETTING["use_exact_edge"] = 0
        constants.BETTING["auto_bankroll"] = 0
        for cfg in constants.SIDE_BETS.values():
            cfg["enabled"] = False

        self.entries = []
        tmp = Path(tempfile.mkdtemp(prefix="bjai_settlement_e2e_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # set_monitor now selects the saved profile for the exact resolution.
        # Keep this synthetic replay isolated from the user's real profiles.
        previous_output_dir = constants.OUTPUT_DIR
        constants.OUTPUT_DIR = tmp / "output"
        self.addCleanup(setattr, constants, "OUTPUT_DIR", previous_output_dir)
        with patch("lib.logic.engine.SessionStore", lambda: SessionStore(tmp / "session.db")):
            self.eng = DetectionEngine(
                log=lambda msg, level=None: self.entries.append((msg, level)))
        self.eng._warm_advice_job = lambda *a: None
        self.eng._optimal_advice = lambda *a, **k: ("", "white", None)
        def stop_pools():
            for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
                getattr(self.eng, name).shutdown(wait=True, cancel_futures=True)
        self.addCleanup(stop_pools)
        # Recognition and views are exercised by the registry E2E test.
        # This regression isolates deterministic ledger settlement.
        self.eng.set_my_seat(self.A, True)
        self.eng.set_bet_placed(self.BET)

    def tearDown(self):
        constants.BETTING.update(self._betting)
        for key, enabled in self._sidebets.items():
            constants.SIDE_BETS[key]["enabled"] = enabled

    # ------------------------------------------------------------- helpers

    def _seat_points(self, seat_idx, n):
        """n points inside the live seat polygon, pairwise farther apart than
        the same-card dedupe radius, found with the engine's own
        _seat_for_point so any region source (calibrated or default) works."""
        region = self.eng.regions[seat_idx]
        left, top, right, bottom = region.bounds
        gap = constants.SAME_CARD_DISTANCE_PX * self.eng._dist_scale + 10.0
        pts = []
        y = top
        while y <= bottom and len(pts) < n:
            x = left
            while x <= right and len(pts) < n:
                if (self.eng._seat_for_point(x, y) == seat_idx
                        and all((x - px) ** 2 + (y - py) ** 2 >= gap * gap
                                for px, py in pts)):
                    pts.append((float(x), float(y)))
                x += 12.0
            y += 12.0
        self.assertEqual(len(pts), n,
                         f"could not place {n} spaced cards in seat {seat_idx}")
        return pts

    def logged(self, fragment, level=None):
        return [(m, lv) for m, lv in self.entries
                if fragment in m and (level is None or lv == level)]

    def frame(self, dealer_preds, player_preds):
        """One detection cycle: _detect's locked section + run_cycle's
        snapshot publish, with synthetic predictions instead of inference."""
        eng = self.eng
        with eng._lock:
            eng._process_dealer(list(dealer_preds))
            eng._process_players(list(player_preds))
            eng._maybe_auto_new_round(list(player_preds))
        eng.publish_snapshot()

    def deal(self, up_pred, deal_preds):
        """Feed the initial-deal frames: both seats' first two cards plus the
        dealer up-card, until the up-card confirms."""
        eng = self.eng
        self.frame([up_pred], deal_preds)
        # Neither card enters the shoe on a single unconfirmed frame.
        for idx in (self.A, self.B):
            self.assertEqual(len(eng.seats[idx].cards),
                             0 if constants.CONFIRM_INITIAL_CARDS_ENABLED else 1)
        if constants.DEALER_CONFIRM_FRAMES > 1:
            self.assertFalse(eng.dealer_locked)
        for _ in range(max(2, constants.EXTRA_CARD_CONFIRM_CYCLES, constants.DEALER_CONFIRM_FRAMES) - 1):
            self.frame([up_pred], deal_preds)
        for idx in (self.A, self.B):
            self.assertEqual(len(eng.seats[idx].cards), 2)
        self.assertTrue(eng.dealer_locked)
        with eng._lock:
            self.assertEqual(eng._activity(), "complete")

    def hit(self, seat_idx, hit_pred, dealer_preds, table_preds):
        """A hit card appears: it must survive EXTRA_CARD_CONFIRM_CYCLES
        consecutive cycles before it locks."""
        eng = self.eng
        before = len(eng.seats[seat_idx].cards)
        self.frame(dealer_preds, table_preds + [hit_pred])
        for _ in range(constants.EXTRA_CARD_CONFIRM_CYCLES - 1):
            self.assertEqual(len(eng.seats[seat_idx].cards), before,
                             "hit locked before its confirmation window")
            self.frame(dealer_preds, table_preds + [hit_pred])
        self.assertEqual(len(eng.seats[seat_idx].cards), before + 1)

    def dealer_draws(self, ranks, up_pred, table_preds):
        """Dealer playout cards appear after the up-card lock; they confirm
        across EXTRA_CARD_CONFIRM_CYCLES cycles like player hits."""
        eng = self.eng
        before = len(eng.dealer_extras)
        dealer_frame = [up_pred] + [dealer_pred(r, DEALER_DRAW_XY[i])
                                    for i, r in enumerate(ranks)]
        self.frame(dealer_frame, table_preds)
        for _ in range(constants.EXTRA_CARD_CONFIRM_CYCLES - 1):
            self.assertEqual(len(eng.dealer_extras), before,
                             "dealer draw locked before its window")
            self.frame(dealer_frame, table_preds)
        self.assertEqual(len(eng.dealer_extras), before + len(ranks))
        self.assertEqual(sorted(c["rank"] for c in eng.dealer_extras[before:]),
                         sorted(ranks))

    def clear_table(self, lingering_up=None):
        """Drive the auto-reset gate: EMPTY_FRAMES_FOR_RESET empty player
        frames with the dealer area clear for the same stretch. With
        `lingering_up` the dealer model keeps seeing the up-card for two
        frames first — the recheck must hold the round through them."""
        eng = self.eng
        start = eng.round_number
        if lingering_up is not None:
            for _ in range(2):
                self.frame([lingering_up], [])
            self.assertEqual(eng.round_number, start,
                             "reset fired while the dealer area was occupied")
        for _ in range(constants.EMPTY_FRAMES_FOR_RESET - 1):
            self.frame([], [])
            self.assertEqual(eng.round_number, start,
                             "reset fired before the empty-frame window")
        self.frame([], [])
        self.assertEqual(eng.round_number, start + 1, "auto reset did not fire")
        self.assertTrue(all(not s.cards for s in eng.seats))
        self.assertIsNone(eng.dealer_card)
        self.assertEqual(eng.dealer_extras, [])

    # ---------------------------------------------------------------- test

    def test_manual_ledger_round_settles_owned_hand(self):
        eng = self.eng
        eng.replace_card(self.A, 0, "10 of Hearts")
        eng.replace_card(self.A, 1, "King of Spades")
        eng.replace_card(self.B, 0, "10 of Clubs")
        eng.replace_card(self.B, 1, "2 of Diamonds")
        eng.replace_dealer("King")
        eng.add_dealer_extra("7")

        eng.new_round()

        self.assertEqual(eng.session_pnl,
                         {"units": 1.0, "eur": 50.0, "side_eur": 0.0, "rounds": 1})
        self.assertEqual(eng.counter.cards_seen, 6)
        self.assertEqual(eng.get_snapshot()["session_pnl"], eng.session_pnl)
        self.assertTrue(all(not seat.cards for seat in eng.seats))
