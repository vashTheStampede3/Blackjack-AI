import unittest

from lib.logic.window_capture import (WindowSource, source_descriptor, window_display_labels,
                                      source_review_required)
from lib.interfaces.ui_actions import window_visibility_state


class WindowLabelsUnitTests(unittest.TestCase):
    def test_labels_are_compact_normalized_and_duplicates_get_pid_tiebreaker(self):
        windows = [WindowSource(1, "  Game   Table  " + "X" * 80, 7, 1600, 1028,
                                application="VirtualBox.exe", minimized=True),
                   WindowSource(2, "Table", 8, 800, 600, application="App.exe"),
                   WindowSource(3, "Table", 9, 800, 600, application="App.exe")]
        labels = window_display_labels(windows)
        self.assertTrue(labels[0].startswith("VirtualBox · Game Table"))
        self.assertIn("1600×1028", labels[0])
        self.assertIn("minimized", labels[0])
        self.assertIn("PID 8", labels[1])
        self.assertIn("PID 9", labels[2])
        self.assertNotIn("[7]", labels[0])
        self.assertTrue(callable(windows[0].ready_source))
        self.assertTrue(callable(windows[0].grab_bgr))

    def test_window_identity_descriptor_omits_process_identity(self):
        source = WindowSource(99, "Table", 123, 1600, 1028, application="VirtualBox.exe", class_name="Chrome_WidgetWin_1")
        descriptor = source_descriptor(source, "Window")
        self.assertEqual(descriptor, {"kind": "window", "source_id": "virtualbox|chrome_widgetwin_1",
                                      "width": 1600, "height": 1028})

    def test_identical_windows_from_same_process_still_have_distinct_labels(self):
        windows = [WindowSource(101, "Table", 42, 1280, 720, application="App.exe",
                                class_name="WindowClass"),
                   WindowSource(202, "Table", 42, 1280, 720, application="App.exe",
                                class_name="WindowClass")]
        labels = window_display_labels(windows)
        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(labels[0].endswith("Window 1"))
        self.assertTrue(labels[1].endswith("Window 2"))

    def test_dialog_visibility_waits_for_map_or_timeout_and_distinguishes_closed(self):
        self.assertEqual(window_visibility_state(exists=True, attempts_left=2), "pending")
        self.assertEqual(window_visibility_state(exists=True, mapped=True), "visible")
        self.assertEqual(window_visibility_state(exists=False), "closed")
        self.assertEqual(window_visibility_state(exists=True, error=True), "error")
        self.assertEqual(window_visibility_state(exists=True), "hidden")

    def test_saved_source_mismatch_or_ambiguity_requires_review(self):
        descriptor = {"kind": "window", "source_id": "app|class", "width": 1280, "height": 720}
        self.assertFalse(source_review_required(descriptor, descriptor))
        self.assertTrue(source_review_required(descriptor, {**descriptor, "source_id": "other|class"}))
        self.assertTrue(source_review_required(descriptor, descriptor, ambiguous=True))
        self.assertTrue(source_review_required(None, descriptor, has_calibration=True))
        self.assertFalse(source_review_required(None, descriptor, has_calibration=False))


if __name__ == "__main__":
    unittest.main()

