"""Pure Mini Mesa state/layout checks; no Tk window or app is launched."""
import unittest

from lib.interfaces.round_overlay import (MiniTableOverlay, card_chip_position,
                                          confirmed_slot, group_controls_y, group_page, group_x,
                                          overlay_visibility, supports_suit_correction)


class MiniTableState(unittest.TestCase):
    def test_stopped_and_confirmed_clean_table_remain_visible(self):
        self.assertEqual(overlay_visibility({"card_groups": [{"cards": [1]}]}, False),
                         (True, "DETECCIÓN DETENIDA"))
        self.assertEqual(overlay_visibility({"reading": {"table_clean_confirmed": True},
                                             "card_groups": [{"cards": [1]}]}, True),
                         (True, "ESPERANDO CARTAS"))

    def test_idle_without_cards_remains_visible_and_editing_has_priority(self):
        snap = {"reading": {}, "card_groups": []}
        self.assertEqual(overlay_visibility(snap, True), (True, "ESPERANDO CARTAS"))
        self.assertEqual(overlay_visibility(snap, True, editing=True),
                         (True, "AJUSTE DE POSICIÓN"))
        self.assertEqual(overlay_visibility(snap, True, editing=True, hud_on=False),
                         (False, ""))

    def test_uncertain_reading_shows_warning_not_advice(self):
        snap = {"count": {"count_uncertain": True}, "card_groups": [{"cards": [{}]}]}
        visible, notice = overlay_visibility(snap, True)
        self.assertTrue(visible)
        self.assertIn("SIN CONSEJO", notice)

    def test_slots_are_zero_based_from_left_and_j1_is_rightmost(self):
        self.assertEqual(confirmed_slot({"slot_index": 3}), 3)
        self.assertIsNone(confirmed_slot({"seat_id": "J4"}))
        self.assertIsNone(confirmed_slot({"slot_index": 8}))
        self.assertEqual(group_x({"seat_id": "J1"}), 840)
        self.assertEqual(group_x({"seat_id": "J3"}), 566)
        self.assertEqual(group_x({"slot_index": 0, "seat_id": "J1"}), 18)
        # Empty seat IDs are not drawn; J2's gap remains between these x slots.
        self.assertEqual(group_x({"seat_id": "J3"})-group_x({"seat_id": "J1"}), -274)

    def test_normalized_bbox_positions_unassigned_group(self):
        x = group_x({"bbox": {"cx": 0.75}}, 2)
        self.assertEqual(x, 684)

    def test_stale_or_ambiguous_advice_is_withdrawn(self):
        self.assertIn("SIN CONSEJO", MiniTableOverlay._advice_text({
            "reading": {"stale": True}, "seats": [{"mine": True, "optimal": "Hit"}]}))
        self.assertIn("SIN CONSEJO", MiniTableOverlay._advice_text({
            "card_groups": [{"is_ambiguous": True}],
            "seats": [{"mine": True, "optimal": "Hit"}]}))

    def test_local_group_pages_bound_cards_and_clamp_old_page(self):
        cards = list(range(19))
        page, index, pages = group_page(cards, 2, 8)
        self.assertEqual((page, index, pages), (cards[16:19], 2, 3))
        page, index, pages = group_page(cards, 99, 4)
        self.assertEqual((page, index, pages), ([16, 17, 18], 4, 5))

    def test_card_chip_positions_stay_inside_group_and_page_controls_band(self):
        for split_hand in (None, 0, 1):
            count = 8 if split_hand is None else 4
            for i in range(count):
                x, y, (w, h) = card_chip_position(i, split_hand, x=18, y=105)
                self.assertGreaterEqual(x, 18)
                self.assertLessEqual(x+w, 18+132)
                self.assertGreaterEqual(y, 105)
                self.assertLessEqual(y+h, 209)
        self.assertGreater(group_controls_y(), 194)
        self.assertLessEqual(group_controls_y()+4, 209)

    def test_suit_correction_is_disabled_for_legacy_references(self):
        self.assertTrue(supports_suit_correction({"card_id": "shoe:1"}))
        self.assertFalse(supports_suit_correction({"legacy_ref": {"seat_index": 0}}))


if __name__ == "__main__":
    unittest.main()
