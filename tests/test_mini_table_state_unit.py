"""Pure Mini Mesa presentation state/layout checks; no Tk or app launch."""
import unittest

from lib.interfaces.round_overlay import MiniTableOverlay, card_chip_position, overlay_visibility


class MiniTableState(unittest.TestCase):
    def test_stopped_and_waiting_tables_remain_visible(self):
        self.assertEqual(overlay_visibility({"status": "ready", "message": "Mano lista"}, False),
                         (True, "DETECCIÓN DETENIDA"))
        self.assertEqual(overlay_visibility({"status": "waiting", "message": "Esperando cartas"}, True),
                         (True, "Esperando cartas"))

    def test_idle_without_cards_remains_visible_and_editing_has_priority(self):
        state = {"status": "waiting", "message": "Esperando cartas", "players": []}
        self.assertEqual(overlay_visibility(state, True), (True, "Esperando cartas"))
        self.assertEqual(overlay_visibility(state, True, editing=True), (True, "AJUSTE DE POSICIÓN"))
        self.assertEqual(overlay_visibility(state, True, editing=True, hud_on=False), (False, ""))

    def test_uncertain_reading_shows_warning_not_advice(self):
        state = {"status": "uncertain", "message": "Lectura dudosa"}
        visible, notice = overlay_visibility(state, True)
        self.assertTrue(visible)
        self.assertIn("SIN CONSEJO", notice)

    def test_single_chip_is_centered_without_seat_identity(self):
        x, _, (w, _) = card_chip_position(0, 1)
        self.assertEqual(x + w / 2, 500)

    def test_chips_are_ordered_and_symmetric(self):
        positions = [card_chip_position(i, 7) for i in range(7)]
        self.assertTrue(all(a[0] < b[0] for a, b in zip(positions, positions[1:])))
        self.assertEqual(positions[0][0] + positions[-1][0] + 27, 1000)

    def test_nonready_or_unlinked_advice_is_withdrawn(self):
        for status in ("reading", "uncertain", "stopped", "waiting", "error", "ready"):
            self.assertEqual(MiniTableOverlay._advice_text({
                "status": status, "advice_available": False,
                "seats": [{"mine": True, "optimal": "Hit"}]}), "SIN CONSEJO")

    def test_long_hand_has_bounded_positions_without_dropping_cards(self):
        positions = [card_chip_position(i, 40) for i in range(40)]
        self.assertEqual(len(positions), 40)
        self.assertGreaterEqual(positions[0][0], 40)
        self.assertLessEqual(positions[-1][0] + 27, 960)

    def test_split_chips_stay_inside_their_own_hand_area(self):
        for center in (320, 680):
            for i in range(15):
                x, y, (w, h) = card_chip_position(i, 15, center, 174, 300)
                self.assertGreaterEqual(x, center - 150)
                self.assertLessEqual(x + w, center + 150)
                self.assertEqual(y + h, 208)

    def test_card_correction_entry_points_are_not_exposed(self):
        for name in ("_draw_picker", "_select_card", "_emit", "_toggle_suit", "update_from_snapshot"):
            self.assertFalse(hasattr(MiniTableOverlay, name))


if __name__ == "__main__":
    unittest.main()
