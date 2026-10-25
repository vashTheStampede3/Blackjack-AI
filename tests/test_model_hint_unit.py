import unittest

from lib.interfaces.modern_gui import format_model_runtime_hint


class ModelRuntimeHintTests(unittest.TestCase):
    def test_uninitialized_metadata_does_not_claim_sidecar_as_runtime(self):
        self.assertEqual(format_model_runtime_hint({
            "players": {"loaded": False, "artifact": "player_cards.onnx", "version": "sidecar"},
            "dealer": {"loaded": False, "artifact": "dealer_cards.onnx"},
        }), "Models: not loaded")

    def test_loaded_identity_shows_only_available_runtime_facts(self):
        self.assertEqual(format_model_runtime_hint({
            "players": {"loaded": True, "artifact": "player_cards_int8.onnx",
                        "version": "v4", "quantization": "int8"},
            "dealer": {"loaded": False, "artifact": "dealer_cards.onnx"},
        }), "Model: player_cards_int8.onnx v4 int8")

    def test_duplicate_model_facts_are_compact(self):
        item = {"loaded": True, "artifact": "same.onnx", "version": None,
                "quantization": "unknown"}
        self.assertEqual(format_model_runtime_hint({"players": item, "dealer": dict(item)}),
                         "Model: same.onnx")


if __name__ == "__main__":
    unittest.main()

