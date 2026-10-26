"""Deterministic real-widget integration from synthetic detections through both views."""
import tkinter as tk
import time
from types import SimpleNamespace

import numpy as np
import pytest


def test_synthetic_detection_to_registry_presentation_real_tk_views_and_correction(
        tmp_path, monkeypatch):
    from lib.common import constants
    from lib.logic.app_modes import INDIVIDUAL
    from lib.logic.card_layout import CardLayout
    from lib.logic.engine import DetectionEngine
    from lib.logic.region_profiles import set_card_layout
    from lib.logic.table_inference import prepare_layout_image
    from lib.logic.table_presentation import build_table_presentation
    from lib.interfaces.table_view import TableView
    from lib.interfaces.round_overlay import MiniTableOverlay

    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(constants, "TRAINING", dict(constants.TRAINING, enabled=0))
    monkeypatch.setattr(constants, "OCR", dict(constants.OCR, enabled=0))
    monkeypatch.setattr(constants, "PHASE", dict(constants.PHASE, enabled=0))
    monkeypatch.setattr(constants, "ANCHORS", dict(constants.ANCHORS, enabled=0))
    monkeypatch.setitem(constants.BETTING, "use_exact_edge", 0)
    width, height = 640, 480
    layout = CardLayout.from_payload({
        "mode": INDIVIDUAL, "resolution": [width, height],
        "mine": [[20, 230], [420, 230], [420, 450], [20, 450]],
        "dealer": [[440, 20], [620, 20], [620, 150], [440, 150]],
    }, (width, height))
    set_card_layout(layout, profile="Default")
    packed, patches = prepare_layout_image(np.zeros((height, width, 3), np.uint8), layout)
    regions = {patch["role"]: patch["packed"] for patch in patches}
    mx, my, mr, mb = regions["mine"]
    dx, dy, dr, db = regions["dealer"]
    observations = [
        {"class": "6", "cx": mx + (mr-mx)*.45, "cy": my + (mb-my)*.52,
         "width": 40, "height": 60, "confidence": .99},
        {"class": "10", "cx": mx + (mr-mx)*.55, "cy": my + (mb-my)*.52,
         "width": 40, "height": 60, "confidence": .99},
        {"class": "A", "cx": dx + (dr-dx)*.5, "cy": dy + (db-dy)*.5,
         "width": 40, "height": 60, "confidence": .99},
    ]

    class SyntheticModel:
        hosted = False
        def predict(self, _image, *_args):
            return [dict(prediction) for prediction in observations]

    class Provider:
        backend_name = "Synthetic integration fixture"
        model = SyntheticModel()
        def players_model(self): return self.model
        def dealer_model(self): return self.model
        def has_hosted(self): return False
        def loaded_model_metadata(self): return {}
        def refresh(self, **_kwargs): return False

    monkeypatch.setattr(constants, "EXTRA_CARD_CONFIRM_CYCLES", 1)
    engine = DetectionEngine(log=lambda *_a, **_k: None, provider=Provider(), persist=False)
    monitor = SimpleNamespace(x=0, y=0, width=width, height=height,
                              name="synthetic", is_primary=True)
    engine.set_monitor(monitor, profile="Default", mode=INDIVIDUAL)
    frame = np.full((height, width, 3), 80, np.uint8)
    engine.capture.grab_bgr = lambda: frame.copy()
    engine._frame_unchanged = lambda _frame: False
    root = tk.Tk()
    root.geometry("1000x700+20+20")
    clicks = []
    table = None
    overlay = None
    try:
        for _ in range(3):
            engine.run_cycle()
        snapshot = engine.get_snapshot()
        assert snapshot["count"]["cards_seen"] == 3
        assert engine.counter.running_count == -1
        presentation = build_table_presentation(
            snapshot, selected_mode=INDIVIDUAL, running=True)
        assert presentation["status"] == "ready", presentation
        assert len(presentation["players"]) == 1
        hero_cards = presentation["players"][0]["cards"]
        dealer_cards = presentation["dealer"]["cards"]
        assert len(hero_cards) == 2 and len(dealer_cards) == 1
        assert len({card["card_id"] for card in hero_cards + dealer_cards}) == 3

        table = TableView(root, on_card_identity_click=clicks.append)
        table.canvas.pack(fill=tk.BOTH, expand=True)
        table.update(presentation)
        overlay = MiniTableOverlay(root, on_correct_card=clicks.append)
        overlay.update_from_presentation(
            presentation, running=True,
            metrics={"running": snapshot["count"]["running"],
                     "true": snapshot["count"]["true"],
                     "cards_seen": snapshot["count"]["cards_seen"],
                     "decks_remaining": snapshot["count"]["decks_remaining"]})
        root.update()
        time.sleep(.15)
        root.update()
        assert table._last_snapshot == overlay._last_state == presentation
        table_ids = [card.get("card_id") for player in presentation["players"]
                     for card in player["cards"]]
        mini_ids = [record["identity"].get("card_id") for record in overlay._drawn_cards]
        assert mini_ids == [dealer_cards[0]["card_id"], *table_ids]

        # A real TableView card widget dispatches the registry identity.
        physical_id = next(card["card_id"] for card in hero_cards if card["rank"] == "6")
        hero = next(seat for seat in table.seats if seat["is_hero"])
        hero_widget = next(widget for widget in hero["cards"]
                           if widget.card_identity["card_id"] == physical_id)
        hero_widget.event_generate("<Button-1>")
        root.update()
        assert clicks and clicks[-1]["card_id"] == physical_id

        result = engine.apply_correction(physical_id, presentation["round_id"],
                                         "set_rank", new_rank="10")
        assert result.ok and result.rc_delta == -2
        engine.run_cycle()
        corrected_snapshot = engine.get_snapshot()
        assert corrected_snapshot["count"]["cards_seen"] == 3
        corrected = build_table_presentation(
            corrected_snapshot, selected_mode=INDIVIDUAL, running=True)
        table.update(corrected)
        overlay.update_from_presentation(corrected, running=True,
            metrics={"running": corrected_snapshot["count"]["running"],
                     "true": corrected_snapshot["count"]["true"],
                     "cards_seen": corrected_snapshot["count"]["cards_seen"],
                     "decks_remaining": corrected_snapshot["count"]["decks_remaining"]})
        root.update()
        assert table._last_snapshot == overlay._last_state == corrected
        assert next(card for card in corrected["players"][0]["cards"]
                    if card["card_id"] == physical_id)["rank"] == "10"
        corrected_rc = engine.counter.running_count
        result = engine.apply_correction(physical_id, corrected["round_id"],
                                         "set_rank", new_rank="10", new_suit="H")
        assert result.ok and result.rc_delta == 0
        assert engine.counter.running_count == corrected_rc

        assert not engine.apply_correction(
            physical_id, corrected["round_id"] + 1, "set_rank", new_rank="9").ok
        assert engine.counter.running_count == corrected_rc
        engine.new_round()
        after_round = engine.get_snapshot()
        assert after_round["count"]["cards_seen"] == 3
        assert build_table_presentation(after_round, selected_mode=INDIVIDUAL,
                                         running=True)["players"][0]["cards"] == []
        engine.reset_shoe()
        after_shoe = engine.get_snapshot()
        assert after_shoe["count"]["cards_seen"] == 0
        assert after_shoe["count"]["running"] == 0
    finally:
        if overlay is not None:
            overlay.close()
        if table is not None:
            table.canvas.destroy()
        root.destroy()
        for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
            getattr(engine, name).shutdown(wait=True, cancel_futures=True)
