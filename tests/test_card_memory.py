"""Card-ledger rules with invented detections: logic, not real recognition."""
import math
import pytest

from lib.logic.card_memory import CardMemory
from lib.logic.counting import CardCounter


def card(rank="8", x=80., owner="other", **kwargs):
    return {"name": rank, "class": rank, "cx": x, "cy": 100., "width": 20., "height": 30.,
            "confidence": .95, "owner": owner, **kwargs}


def memory():
    counter = CardCounter()
    return counter, CardMemory(counter)


def test_one_identity_one_confirmation_and_repeated_frames_do_not_recount():
    counter, ledger = memory()
    boxes = [card("5", 80), card("King", 120)]
    ledger.observe(boxes + [dict(boxes[0])], 1.)
    assert counter.cards_seen == 0 and counter.count_uncertain
    ledger.observe(boxes, 2.)
    assert counter.cards_seen == 2 and counter.running_count == 0 and not counter.count_uncertain
    identities = set(ledger.cards)
    for now in range(3, 8):
        ledger.observe(boxes, float(now))
    assert counter.cards_seen == 2 and set(ledger.cards) == identities


def test_rank_conflict_requires_identity_correction_and_changes_count_once():
    counter, ledger = memory()
    for now in (1., 2.):
        ledger.observe([card("5")], now)
    identity = next(iter(ledger.cards))
    ledger.observe([card("King")], 3.)
    assert counter.cards_seen == 1 and counter.running_count == 1 and counter.count_uncertain
    ledger.correct(identity, "King", expected_round=1)
    assert counter.cards_seen == 1 and counter.running_count == -1 and not counter.count_uncertain
    ledger.observe([card("5")], 4.)
    assert counter.running_count == -1  # manual correction wins over the model


def test_unread_card_and_shoe_uncertainty_survive_round_clear_and_state_restore():
    counter, ledger = memory()
    ledger.observe([card(None)], 1.)
    identity = next(iter(ledger.cards))
    for now in range(2, 8):
        ledger.observe([], float(now))
    assert ledger.clear_ready
    ledger.clear_round()
    restored = CardCounter()
    restored.apply_state(counter.get_state())
    assert restored.count_uncertain and restored.cards_seen == 0
    next_ledger = CardMemory(restored)
    before = restored.get_state()
    with pytest.raises(ValueError, match="card_not_found"):
        next_ledger.correct(identity, "6")
    assert restored.get_state() == before
    assert restored.running_count == 0 and restored.cards_seen == 0 and restored.count_uncertain


def test_confirmed_round_clear_keeps_shoe_count_and_new_round_gets_new_ids():
    counter, ledger = memory()
    ledger.observe([card("6")], 1.)
    ledger.observe([card("6")], 2.)
    previous = set(ledger.cards)
    ledger.clear_round()
    assert counter.cards_seen == 1 and counter.running_count == 1
    ledger.observe([card("6")], 10.)
    ledger.observe([card("6")], 11.)
    assert not previous.intersection(ledger.cards) and counter.cards_seen == 2


def test_wrong_round_shoe_and_bad_rank_corrections_do_not_mutate_count():
    counter, ledger = memory()
    ledger.observe([card("6")], 1.)
    ledger.observe([card("6")], 2.)
    identity = next(iter(ledger.cards))
    original = counter.get_state()
    with pytest.raises(ValueError, match="ronda"):
        ledger.correct(identity, "King", expected_round=2)
    with pytest.raises(ValueError):
        ledger.correct(identity, "invalid")
    assert counter.get_state() == original
    counter.reset_shoe()
    with pytest.raises(ValueError, match="shoe"):
        ledger.correct(identity, "King")
    assert counter.cards_seen == 0


def test_manual_false_positive_rejection_does_not_return_as_a_new_count():
    counter, ledger = memory()
    ledger.observe([card("5")], 1.)
    identity = next(iter(ledger.cards))
    ledger.correct(identity, None)
    for now in range(2, 6):
        ledger.observe([card("5")], float(now))
    assert counter.cards_seen == 0 and not counter.count_uncertain


def test_shifted_same_rank_without_old_position_stays_ambiguous():
    counter, ledger = memory()
    ledger.observe([card("8", 80)], 1.)
    ledger.observe([card("8", 80)], 2.)
    assert counter.cards_seen == 1 and not counter.count_uncertain

    # Could be the counted card moved, or a second 8 appeared after the first
    # became occluded. Position and rank alone cannot prove which.
    ledger.observe([card("8", 100)], 3.)
    ledger.observe([card("8", 100)], 4.)
    assert counter.cards_seen == 1
    assert counter.count_uncertain
    assert len(ledger.cards) == 2


def test_one_frame_candidate_may_settle_at_new_position_before_confirmation():
    counter, ledger = memory()
    ledger.observe([card("8", 80)], 1.)
    ledger.observe([card("8", 94)], 2.)
    ledger.observe([card("8", 94)], 3.)
    assert counter.cards_seen == 1 and not counter.count_uncertain


def test_two_same_rank_cards_cross_without_changing_hilo_count():
    counter, ledger = memory()
    initial = [card("8", 80), card("8", 140)]
    ledger.observe(initial, 1.)
    ledger.observe(initial, 2.)
    ledger.observe([card("8", 140), card("8", 80)], 3.)
    assert counter.cards_seen == 2 and counter.running_count == 0
    assert not counter.count_uncertain  # identity swap cannot alter Hi-Lo


def test_split_like_pair_motion_marks_shoe_uncertain_instead_of_guessing():
    counter, ledger = memory()
    pair = [card("8", 80, "mine"), card("8", 140, "mine")]
    ledger.observe(pair, 1.)
    ledger.observe(pair, 2.)
    ledger.observe([card("8", 55, "mine"), card("8", 165, "mine")], 3.)
    assert counter.cards_seen == 2
    assert counter.count_uncertain


def test_occluded_counted_card_reappears_without_being_counted_twice():
    counter, ledger = memory()
    for now in (1., 2.):
        ledger.observe([card("5", 80)], now)
    ledger.observe([], 3.)
    ledger.observe([card("5", 80)], 4.)
    assert counter.cards_seen == 1 and counter.running_count == 1
    assert not counter.count_uncertain


def test_rotated_extra_card_counts_once_when_rank_is_read():
    counter, ledger = memory()
    hand = [card("6", 80, "mine"), card("9", 120, "mine")]
    ledger.observe(hand, 1.)
    ledger.observe(hand, 2.)
    rotated_extra = card("5", 165, "mine", width=35., height=15.)
    ledger.observe(hand + [rotated_extra], 3.)
    ledger.observe(hand + [rotated_extra], 4.)
    ledger.observe(hand + [rotated_extra], 5.)
    assert counter.cards_seen == 3 and counter.running_count == 2
    assert not counter.count_uncertain


def test_unread_rotated_extra_keeps_shoe_uncertain_after_clear():
    counter, ledger = memory()
    hand = [card("6", 80, "mine"), card("9", 120, "mine")]
    ledger.observe(hand, 1.)
    ledger.observe(hand, 2.)
    ledger.observe(hand + [card(None, 165, "mine", width=35., height=15.)], 3.)
    assert counter.cards_seen == 2 and counter.count_uncertain
    ledger.clear_round()
    assert counter.count_uncertain


def test_crossing_same_rank_between_owners_blocks_advice_without_recount():
    counter, ledger = memory()
    initial = [card("8", 80, "mine"), card("8", 140, "other")]
    ledger.observe(initial, 1.)
    ledger.observe(initial, 2.)
    ledger.observe([card("8", 80, "other"), card("8", 140, "mine")], 3.)
    assert counter.cards_seen == 2 and counter.running_count == 0
    assert ledger.advice_pending


def test_hidden_dealer_hud_and_new_player_same_rank_are_distinct():
    counter, ledger = memory()
    dealer = card("7", 400, "dealer") | {"cy": 35.}
    player = card("7", 80, "other")
    ledger.observe([dealer], 1.)
    ledger.observe([dealer], 2.)
    ledger.observe([player], 3.)
    ledger.observe([player], 4.)
    assert counter.cards_seen == 2 and not counter.count_uncertain


def test_distant_same_rank_players_are_not_assumed_to_be_one_card():
    counter, ledger = memory()
    ledger.observe([card("8", 80)], 1.)
    ledger.observe([card("8", 80)], 2.)
    ledger.observe([card("8", 500)], 3.)
    ledger.observe([card("8", 500)], 4.)
    assert counter.cards_seen == 2 and not counter.count_uncertain


def test_new_dealer_rank_is_not_absorbed_by_moving_upcard():
    counter, ledger = memory()
    upcard = card("7", 400, "dealer")
    for now in (1., 2.):
        ledger.observe([upcard], now)
    shifted = card("7", 380, "dealer")
    revealed = card("10", 400, "dealer")
    for now in (3., 4., 5.):
        ledger.observe([shifted, revealed], now)
    assert counter.cards_seen == 2
    assert any(c.rank == "10" and c.counted for c in ledger.cards.values())


def test_noisy_read_of_already_confirmed_card_does_not_taint_shoe():
    counter, ledger = memory()
    for now in (1., 2.):
        ledger.observe([card("5")], now)
    ledger.observe([card("King", unstable=True)], 3.)
    ledger.observe([card("5")], 4.)
    assert counter.cards_seen == 1 and counter.running_count == 1
    assert not counter.count_uncertain


def test_rotated_third_card_closes_group_as_double_without_changing_scale():
    counter, ledger = memory()
    pair = [card("6", 80, "mine"), card("9", 100, "mine")]
    ledger.observe(pair, 1.)
    ledger.observe(pair, 2.)
    extra = card("5", 125, "mine", width=64., height=44.)
    assert extra["width"] / extra["height"] > 1.15
    for now in (3., 4.):
        ledger.observe(pair + [extra], now)
    group = next(g for g in ledger.group_tracker.ordered_groups() if g.is_hero)
    assert counter.cards_seen == 3
    assert group.closed_by_double
    tracked = next(c for c in ledger.cards.values() if c.rank == "5")
    assert tracked.is_rotated_double
    assert math.isclose(tracked.char_scale, math.sqrt(64.*44.))
    assert math.isclose(tracked.char_scale, math.sqrt(44.*64.))


def test_split_like_motion_keeps_one_seat_and_midpoint_hit_stays_ambiguous():
    counter, ledger = memory()
    pair = [card("8", 80, "mine"), card("8", 140, "mine")]
    ledger.observe(pair, 1.)
    ledger.observe(pair, 2.)
    split = [card("8", 55, "mine"), card("8", 165, "mine")]
    ledger.observe(split, 3.)
    ledger.observe(split, 4.)
    group = next(g for g in ledger.group_tracker.ordered_groups() if g.is_hero)
    assert group.has_split
    hit = card("7", 110, "mine")
    ledger.observe(split + [hit], 5.)
    ledger.observe(split + [hit], 6.)
    assert any(c.rank == "7" and c.counted for c in ledger.cards.values())
    assert ledger.advice_pending
    assert counter.cards_seen <= 3  # uncertain moved/new identities never inflate RC


def test_rank_and_suit_are_kept_separately_but_counted_as_one_card():
    counter, ledger = memory()
    for now in (1., 2.):
        ledger.observe([card("8 of Diamonds")], now)
    tracked = next(iter(ledger.cards.values()))
    assert tracked.rank == "8" and tracked.suit == "D"
    assert tracked.counted_rank == "8"
    assert tracked.counted_name == "8 of Diamonds"
    assert counter.cards_seen == 1
    assert counter.seen_counts_by_rank()["8"] == 1
