"""Persist an incomplete shoe even when its first card was never read."""
from lib.common import constants
from lib.logic.counting import CardCounter
from lib.logic.session_store import SessionStore


def test_zero_card_shoe_retains_doubt_through_sqlite_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    store = SessionStore(tmp_path / "shoe.db")
    counter = CardCounter()
    counter.mark_uncertainty("unread", {"reason": "Carta sin leer"})
    store.save_shoe_state(counter.get_state(), 2, False)
    saved = store.load_recent_shoe_state()
    assert saved is not None
    restored = CardCounter()
    restored.apply_state(saved["state"])
    assert restored.cards_seen == 0 and restored.count_uncertain
    restored.clear_uncertainty("unread")
    assert not restored.count_uncertain


def test_new_shoe_clears_doubts_and_clean_empty_shoe_is_not_offered(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    store = SessionStore(tmp_path / "shoe.db")
    counter = CardCounter()
    previous = counter.shoe_id
    counter.mark_uncertainty("unread", {"reason": "Carta sin leer"})
    counter.reset_shoe()
    assert counter.shoe_id != previous and not counter.count_uncertain
    store.save_shoe_state(counter.get_state(), 1, False)
    assert store.load_recent_shoe_state() is None
