import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.logic import region_profiles
from lib.logic.window_capture import WindowSource, source_descriptor, window_display_labels
from lib.interfaces.ui_actions import window_visibility_state


class ProfileSourcesUnitTests(unittest.TestCase):
    def test_resolution_summary_uses_saved_cards_and_keeps_ocr_separate(self):
        item = {"card_resolutions": ["1280x720", "1600x1028"],
                "ocr_resolutions": ["1600x1028"]}
        self.assertEqual(region_profiles.calibration_resolution_summary(item),
                         (("1280x720", "1600x1028"), ("1600x1028",)))
        self.assertEqual(region_profiles.calibration_resolution_summary({}), ((), ()))

    def test_source_context_round_trip_keeps_only_safe_descriptor_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            with patch.object(region_profiles, "store_path", return_value=path):
                context = {"kind": "window", "source_id": "app|table", "width": 1600,
                           "height": 1028, "pid": 1234, "hwnd": 5678, "screenshot": "secret"}
                region_profiles.set_source_context((1600, 1028), context)
                self.assertEqual(region_profiles.get_source_context((1600, 1028)),
                                 {"kind": "window", "source_id": "app|table",
                                  "width": 1600, "height": 1028})


if __name__ == "__main__":
    unittest.main()
