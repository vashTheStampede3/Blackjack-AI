"""Card-ledger rules with invented detections: logic, not real recognition."""
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
    next_ledger.correct(identity, "6")
    assert restored.running_count == 1 and restored.cards_seen == 1 and not restored.count_uncertain


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
