"""Deterministic tests for physical-card ledger and presentation grouping invariants."""
from types import SimpleNamespace

from lib.logic.card_layout import TableGroupTracker
from lib.logic.card_memory import CardMemory
from lib.logic.counting import CardCounter


def _group_card(x, *, seen=1., owner="other", rank="8", width=30, height=40):
    return SimpleNamespace(
        id=f"card-{x}-{seen}", rank=rank, cx=x, cy=100., width=width, height=height,
        first_seen=seen, last_seen=seen, owner=owner, observed_owner=owner,
        state="confirmed", manual_seat_override=None, seat_id=None,
        ambiguous_seat=False, vx=0., vy=0., is_rotated_double=False,
        hand=0,
    )


def test_late_right_group_stays_provisional_and_does_not_rename_existing_labels():
    tracker = TableGroupTracker()
    initial = [_group_card(220), _group_card(100)]
    groups = tracker.update_groups(initial)
    labels_before = {g.seat_id for g in groups}
    assert labels_before == {"J1", "J2"}
    late = _group_card(350, seen=2.)
    groups = tracker.update_groups(initial + [late])
    assert {g.seat_id for g in groups if g.seat_id != "TMP_3"} >= labels_before
    provisional = next(g for g in groups if late in g.cards)
    assert provisional.seat_id.startswith("TMP_")
    assert provisional.is_ambiguous and late.ambiguous_seat


def test_overlapping_repeated_detection_counts_once_and_unread_survives_round_clear():
    counter = CardCounter(deck_count=6)
    memory = CardMemory(counter, confirmations=2, empty_frames=2)
    first = {"name": "8", "cx": 100., "cy": 100., "width": 30., "height": 40.,
             "owner": "mine"}
    memory.observe([first, dict(first)], 1.)
    assert counter.cards_seen == 0
    memory.observe([first], 2.)
    assert counter.cards_seen == 1
    unread = {"name": None, "cx": 170., "cy": 100., "width": 30., "height": 40.,
              "owner": "mine"}
    memory.observe([unread], 3.)
    issue_ids = set(counter.uncertainties)
    memory.clear_round()
    assert issue_ids <= set(counter.uncertainties)
    assert counter.cards_seen == 1


def test_equal_rank_crossing_with_occlusion_never_adds_an_extra_count():
    counter = CardCounter(deck_count=6)
    memory = CardMemory(counter, confirmations=2)
    def pred(x):
        return {"name": "8", "cx": float(x), "cy": 100., "width": 30.,
                "height": 40., "owner": "other"}
    memory.observe([pred(100), pred(220)], 1.)
    memory.observe([pred(100), pred(220)], 2.)
    assert counter.cards_seen == 2
    memory.observe([], 2.5)
    memory.observe([pred(160)], 3.)
    assert counter.cards_seen == 2
    assert counter.count_uncertain
    issue_ids = set(counter.uncertainties)
    memory.clear_round()
    assert issue_ids <= set(counter.uncertainties)
    assert counter.cards_seen == 2


def test_split_middle_card_is_unassigned_and_blocks_advice():
    counter = CardCounter(deck_count=6)
    memory = CardMemory(counter)
    roots = [_group_card(100, owner="mine"), _group_card(200, owner="mine", seen=2.)]
    third = _group_card(150, owner="mine", seen=3.)
    for card in roots + [third]:
        card.counted = True
        card.counted_name = card.rank
        card.counted_rank = card.rank
        card.round_id = 1
        memory.cards[card.id] = card
    memory.split_roots = [roots[0].id, roots[1].id]
    memory._group_hands()
    assert third.hand is None
    assert memory.advice_pending


def test_rotated_last_card_only_marks_possible_double():
    tracker = TableGroupTracker()
    cards = [_group_card(100 + i * 2, seen=i, width=60 if i == 2 else 30,
                         height=30 if i == 2 else 40) for i in range(3)]
    cards[-1].is_rotated_double = True
    group = tracker.update_groups(cards)[0]
    assert group.closed_by_double
    assert not any(getattr(c, "bet_confirmed", False) for c in cards)


def test_rank_correction_and_rejection_change_count_exactly_once():
    counter = CardCounter(deck_count=6)
    memory = CardMemory(counter, confirmations=1)
    memory.observe([{"name": "8", "cx": 40., "cy": 50., "width": 30.,
                     "height": 40., "owner": "mine"}], 1.)
    card_id = next(iter(memory.cards))
    assert counter.cards_seen == 1
    fixed = memory.apply_correction(card_id, 1, "set_rank", new_rank="9")
    assert fixed.ok and counter.cards_seen == 1
    assert counter.per_rank["8"] == 0 and counter.per_rank["9"] == 1
    discarded = memory.apply_correction(card_id, 1, "not_a_card")
    assert discarded.ok and counter.cards_seen == 0
    assert counter.per_rank["9"] == 0
    again = memory.apply_correction(card_id, 1, "not_a_card")
    assert not again.ok and counter.cards_seen == 0


def test_unknown_ambiguous_stale_or_missing_dealer_withdraws_advice():
    from lib.logic.engine import DetectionEngine
    gate = DetectionEngine._advice_withdrawal_reason
    assert gate(None) is not None
    assert gate("10", hero_ambiguous=True) is not None
    assert gate("10", registry_pending=True) is not None
    assert gate("10", dealer_uncertain=True) is not None
    assert gate("10", stale_reading=True) is not None
    assert gate("10", count_uncertain=True) is not None
    assert gate("10") is None
