"""Specialized 2D digital card detector for dealer HUD and 1v1 individual mode.

Digital cards in online casino interfaces feature:
- Clean, upright rectangular card backgrounds with uniform high contrast against felt.
- Zero-degree rotation (exact vertical alignment).
- Crisp typographic glyphs for ranks 2..10, J, Q, K, A in the top-left corners.
- Horizontal strips of 1 to 5 cards laid out left-to-right with potential partial overlap.

Provides:
- Multi-scale normalized template matching against canonical asset cards and typography.
- Horizontal card strip segmentation that prevents adjacent cards from collapsing.
- Adaptive upscaling + padding for small HUD crops.
- Candidate conversion and prediction formatting interoperable with CardMemory.
"""

from copy import deepcopy
import glob
import math
import os
from pathlib import Path

import cv2
import numpy as np

from ..common.card_mappings import DEALER_CLASS_MAP, PLAYER_CLASS_MAP
from .vision_labels import RANKS, canonical_rank

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets" / "cards"


class DigitalCardDetector:
    """Specialized detector for 2D digital card representations in online casino HUDs."""

    _instance = None

    def __init__(self, assets_path=None):
        self.assets_path = Path(assets_path) if assets_path else ASSETS_DIR
        self._templates = []
        self._load_templates()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_templates(self):
        """Precompute normalized glyph templates for all 13 ranks across suits."""
        self._templates.clear()
        suits = ("spades", "hearts", "diamonds", "clubs")
        rank_names = {
            "A": "ace", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6",
            "7": "7", "8": "8", "9": "9", "10": "10", "J": "jack",
            "Q": "queen", "K": "king",
        }

        # 1. Load from assets/cards/ if available
        if self.assets_path.exists():
            for suit in suits:
                for rank, name in rank_names.items():
                    fname = f"{name}_of_{suit}.png"
                    fpath = self.assets_path / fname
                    if fpath.exists():
                        img = cv2.imread(str(fpath), cv2.IMREAD_UNCHANGED)
                        if img is not None and img.size > 0:
                            tpl = self._extract_normalized_glyph(img)
                            if tpl is not None:
                                self._templates.append((rank, suit, tpl))

        # 2. Also synthesize high-contrast typographic glyphs for font-independence
        for rank in RANKS:
            synth_tpl = self._synthesize_font_glyph(rank)
            self._templates.append((rank, "synth", synth_tpl))

    @staticmethod
    def _extract_normalized_glyph(img_bgr):
        """Extract green-channel normalized rank glyph (24x32)."""
        h, w = img_bgr.shape[:2]
        if h < 200:
            factor = 250.0 / max(1, h)
            img_bgr = cv2.resize(img_bgr, (int(w * factor), 250), interpolation=cv2.INTER_CUBIC)
            h, w = img_bgr.shape[:2]
        # Crop top-left region containing the rank
        glyph_crop = img_bgr[int(0.01 * h):int(0.14 * h), int(0.01 * w):int(0.20 * w)]
        if glyph_crop.size == 0:
            return None
        green = glyph_crop[:, :, 1] if glyph_crop.ndim == 3 else glyph_crop
        return cv2.resize(green, (24, 32), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _synthesize_font_glyph(rank):
        """Synthesize normalized (24x32) grayscale rank glyph using standard font."""
        canvas = np.full((64, 48), 255, dtype=np.uint8)
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.9 if rank == "10" else 1.1
        thickness = 2
        text_size = cv2.getTextSize(rank, font, scale, thickness)[0]
        tx = max(0, (48 - text_size[0]) // 2)
        ty = max(0, (64 + text_size[1]) // 2)
        cv2.putText(canvas, rank, (tx, ty), font, scale, 0, thickness, cv2.LINE_AA)
        return cv2.resize(canvas, (24, 32), interpolation=cv2.INTER_AREA)

    def classify_card_crop(self, card_bgr):
        """Classify a single segmented digital card crop.

        Returns (rank, confidence_score) where confidence is in [0.0, 1.0].
        """
        if card_bgr is None or card_bgr.size == 0 or card_bgr.shape[0] < 10 or card_bgr.shape[1] < 10:
            return "unknown", 0.0

        h, w = card_bgr.shape[:2]
        # If card is small, upscale to standard reference scale
        if h < 180:
            factor = 220.0 / h
            card_bgr = cv2.resize(card_bgr, (int(w * factor), 220), interpolation=cv2.INTER_CUBIC)
            h, w = card_bgr.shape[:2]

        glyph_crop = card_bgr[int(0.01 * h):int(0.15 * h), int(0.01 * w):int(0.22 * w)]
        if glyph_crop.size == 0 or float(np.std(glyph_crop)) < 10.0:
            return "unknown", 0.0

        green = glyph_crop[:, :, 1] if glyph_crop.ndim == 3 else glyph_crop
        target_norm = cv2.resize(green, (24, 32), interpolation=cv2.INTER_AREA)

        best_score = -2.0
        best_rank = "unknown"

        for rank, suit, tpl in self._templates:
            try:
                res = cv2.matchTemplate(target_norm, tpl, cv2.TM_CCOEFF_NORMED)
                score = float(res[0, 0])
            except Exception:
                continue

            if score > best_score:
                best_score = score
                best_rank = rank

        if best_score < 0.35:
            return "unknown", 0.0

        # Normalize score from [-1, 1] into [0.0, 1.0]
        confidence = max(0.0, min(1.0, (best_score + 0.15) / 1.15))
        return best_rank, confidence

    @staticmethod
    def segment_cards_in_strip(roi_bgr, expected_min_cards=1, expected_max_cards=5):
        """Segment 1 to 5 horizontal cards in a digital HUD strip.

        Returns a list of bounding boxes [(x, y, w, h), ...] in ROI local coordinates.
        Never collapses adjacent cards in a horizontal sequence.
        """
        if roi_bgr is None or roi_bgr.size == 0:
            return []

        h, w = roi_bgr.shape[:2]
        if h < 20 or w < 20:
            return []

        gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY) if roi_bgr.ndim == 3 else roi_bgr
        if float(np.std(gray)) < 15.0:
            return []

        # In digital casino HUDs, cards have bright rectangular faces (> 130-150 luminance)
        # on darker felt or background
        _, mask = cv2.threshold(gray, 130, 255, cv2.THRESH_BINARY)
        total_pixels = h * w
        white_pixels = int(np.sum(mask > 0))
        if white_pixels < 200 or white_pixels > 0.95 * total_pixels:
            return []

        # Vertical projection to find the card band height
        v_proj = np.sum(mask > 0, axis=1)
        y_indices = np.where(v_proj > 0.08 * w)[0]
        if len(y_indices) < 15:
            return []
        y1, y2 = max(0, y_indices[0] - 2), min(h, y_indices[-1] + 2)

        card_h = max(20, y2 - y1)
        # Expected card aspect ratio: width is ~ 60% to 75% of height
        expected_cw = int(round(card_h * 0.70))

        # Horizontal projection within the vertical card band
        band = mask[y1:y2, :]
        h_proj = np.sum(band > 0, axis=0)
        x_indices = np.where(h_proj > 0.25 * card_h)[0]

        if len(x_indices) < 15:
            return []
        x1, x2 = max(0, x_indices[0] - 2), min(w, x_indices[-1] + 2)

        strip_w = x2 - x1
        if strip_w < 15:
            return []
        num_cards = max(1, min(expected_max_cards, round(strip_w / max(1, expected_cw * 0.85))))
        if num_cards <= 1:
            return [(x1, y1, min(w - x1, expected_cw), card_h)]

        # Find internal vertical edges (card left borders)
        band_bgr = roi_bgr[y1:y2, x1:x2]
        sobelx = cv2.Sobel(band_bgr[:, :, 1] if band_bgr.ndim == 3 else band_bgr, cv2.CV_64F, 1, 0, ksize=3)
        edge_proj = np.sum(np.abs(sobelx), axis=0)

        # Detect left-edge peaks spaced by at least 45% of expected card width
        min_spacing = max(10, int(expected_cw * 0.45))
        peaks = [0]
        for x in range(min_spacing, strip_w - min_spacing):
            window = edge_proj[max(0, x - min_spacing // 2):min(strip_w, x + min_spacing // 2 + 1)]
            if edge_proj[x] == np.max(window) and edge_proj[x] > 0.25 * np.max(edge_proj):
                if x - peaks[-1] >= min_spacing:
                    peaks.append(x)

        # If edge peaks did not find enough cards, use uniform horizontal stepping
        if len(peaks) < num_cards:
            step = strip_w / num_cards
            peaks = [int(round(i * step)) for i in range(num_cards)]

        # Limit to expected max cards
        peaks = peaks[:expected_max_cards]

        boxes = []
        for i, px in enumerate(peaks):
            next_px = peaks[i + 1] if i + 1 < len(peaks) else strip_w
            # Each card has width expected_cw, bounded by strip bounds
            card_x = x1 + px
            avail_w = w - card_x
            cw = min(expected_cw, avail_w)
            boxes.append((card_x, y1, max(15, cw), card_h))

        return boxes

    def detect_digital_strip(self, roi_bgr, role="dealer", offset=(0, 0), min_confidence=0.45):
        """Segment and classify all 2D digital cards in a horizontal strip.

        Returns predictions matching standard Blackjack-AI format with surface='digital'.
        """
        boxes = self.segment_cards_in_strip(roi_bgr)
        predictions = []
        ox, oy = offset

        for bx, by, bw, bh in boxes:
            card_crop = roi_bgr[by:by + bh, bx:bx + bw]
            rank, conf = self.classify_card_crop(card_crop)

            if rank == "unknown" or conf < min_confidence:
                continue

            cx = float(bx + bw / 2.0 + ox)
            cy = float(by + bh / 2.0 + oy)

            pred = {
                "class": rank,
                "name": DEALER_CLASS_MAP.get(rank) or PLAYER_CLASS_MAP.get(rank, rank),
                "rank": rank,
                "cx": cx,
                "cy": cy,
                "width": float(bw),
                "height": float(bh),
                "confidence": float(conf),
                "owner": role,
                "surface": "digital",
                "ambiguous": False,
            }
            predictions.append(pred)

        return predictions

    @staticmethod
    def adaptive_upscale(roi_bgr, min_height=160):
        """Upscale small digital HUD crops so YOLO tensors preserve clean glyphs."""
        h, w = roi_bgr.shape[:2]
        if h >= min_height or h == 0 or w == 0:
            return roi_bgr, 1.0, 0, 0
        factor = min_height / float(h)
        new_w = int(round(w * factor))
        new_h = int(round(h * factor))
        upscaled = cv2.resize(roi_bgr, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        # Pad to divisible by 32
        pad_h = (32 - (new_h % 32)) % 32
        pad_w = (32 - (new_w % 32)) % 32
        padded = cv2.copyMakeBorder(upscaled, 0, pad_h, 0, pad_w, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        return padded, factor, 0, 0


def detect_digital_cards(image_bgr, role="dealer", offset=(0, 0), min_confidence=0.45):
    """Convenience helper for standalone digital card strip detection."""
    detector = DigitalCardDetector.get_instance()
    return detector.detect_digital_strip(image_bgr, role=role, offset=offset, min_confidence=min_confidence)
