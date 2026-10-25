"""Offline calibration frames. Never connected to the detection/counting loop."""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


class ImageSource:
    offline = True

    def __init__(self, frame, resolution=None, name="Image"):
        self.original = frame.copy()
        self.resolution = tuple(resolution or (frame.shape[1], frame.shape[0]))
        self.name = name
        self.crop = (0, 0, frame.shape[1], frame.shape[0])
        self._last_capture_metadata = None
        self._validate(self.crop)

    @classmethod
    def open(cls, path, resolution=None):
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Image too large: use a table screenshot under 40 megapixels.")
            rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
        # Keep a mismatched image for explicit crop alignment, not silent stretch.
        source = cls(rgb[:, :, ::-1].copy(), name=Path(path).name)
        source.resolution = tuple(resolution or source.resolution)
        return source

    def _validate(self, crop):
        l, t, r, b = crop
        h, w = self.original.shape[:2]
        if not (0 <= l < r <= w and 0 <= t < b <= h):
            raise ValueError("Select a rectangle inside the image.")
        tw, th = self.resolution
        if tw < 2 or th < 2:
            raise ValueError("Invalid target resolution.")
        if abs(((r-l)/(b-t)) / (tw/th) - 1) > .01:
            raise ValueError("Image and target have different framing. Use Align crop to select the same table area.")

    def set_crop(self, crop):
        crop = tuple(int(v) for v in crop)
        self._validate(crop)
        self.crop = crop

    def grab_bgr(self):
        self._validate(self.crop)
        l, t, r, b = self.crop
        output = cv2.resize(self.original[t:b, l:r], self.resolution,
                            interpolation=cv2.INTER_LINEAR)
        self._last_capture_metadata = {
            "source_size": [self.original.shape[1], self.original.shape[0]],
            "crop": [l, t, r, b],
            "crop_size": [r-l, b-t],
            "output_size": [output.shape[1], output.shape[0]],
            "scale": [self.resolution[0]/(r-l), self.resolution[1]/(b-t)],
        }
        return output

    @property
    def last_capture_metadata(self):
        """Metadata for the last offline frame transform; no pixels retained."""
        return dict(self._last_capture_metadata) if self._last_capture_metadata else None

    def close_local(self):
        pass
