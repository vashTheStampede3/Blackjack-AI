"""Read a chosen local video frame without models, screen capture, or counting."""
import math
from pathlib import Path

import cv2

from .image_source import ImageSource


def parse_video_time(text):
    """Seconds or mm:ss / hh:mm:ss, allowing a fractional last component."""
    try:
        parts = str(text).strip().split(":")
        if not 1 <= len(parts) <= 3:
            raise ValueError
        values = [float(p) for p in parts]
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError
        if len(values) > 1 and any(v >= 60 for v in values[1:]):
            raise ValueError
        total = 0.0
        for value in values:
            total = total * 60 + value
        return total
    except (TypeError, ValueError):
        raise ValueError("Ingresá segundos o minutos:segundos, por ejemplo 1:12.5.") from None


class VideoFrameSource(ImageSource):
    """A frozen native frame; seeking the video cannot change this image."""
    is_video_frame = True

    def __init__(self, frame, path, frame_index, timestamp_seconds):
        super().__init__(frame, name=f"{Path(path).name} · {timestamp_seconds:.3f} s")
        self.video_path = str(Path(path).resolve())
        self.frame_index = frame_index
        self.timestamp_seconds = timestamp_seconds

    @property
    def calibration_context(self):
        return {"kind": "video_frame", "path": self.video_path,
                "native_resolution": [self.original.shape[1], self.original.shape[0]],
                "resolution": list(self.resolution), "crop": list(self.crop),
                "frame_index": self.frame_index, "timestamp_seconds": self.timestamp_seconds}


class VideoFile:
    """Small local-only reader. Decoding is limited to two CPU threads."""
    def __init__(self, path):
        text = str(path)
        if "://" in text or text.startswith(("\\\\", "//")):
            raise ValueError("Elegí un archivo de video local; no se admiten fuentes de red.")
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError("No se encuentra el archivo de video.")
        self._capture = cv2.VideoCapture(str(self.path), cv2.CAP_FFMPEG,
                                        [cv2.CAP_PROP_N_THREADS, 2])
        try:
            if not self._capture.isOpened():
                raise ValueError("No se pudo abrir el video local.")
            self.resolution = (int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                               int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            self.fps = float(self._capture.get(cv2.CAP_PROP_FPS))
            self.frame_count = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT))
            w, h = self.resolution
            if not (w > 1 and h > 1 and w * h <= 40_000_000
                    and math.isfinite(self.fps) and self.fps > 0 and self.frame_count > 0):
                raise ValueError("El video no informa una resolución o duración válida.")
            self.duration_seconds = self.frame_count / self.fps
            self.last_frame_seconds = (self.frame_count - 1) / self.fps
        except Exception:
            self.close()
            raise

    def frame_at(self, seconds):
        seconds = float(seconds)
        if not math.isfinite(seconds) or not 0 <= seconds < self.duration_seconds:
            raise ValueError("El momento elegido está fuera del video.")
        if self._capture is None:
            raise ValueError("El video está cerrado.")
        index = min(self.frame_count - 1, round(seconds * self.fps))
        if not self._capture.set(cv2.CAP_PROP_POS_FRAMES, index):
            raise ValueError("No se pudo buscar ese fotograma.")
        ok, frame = self._capture.read()
        if not ok:
            raise ValueError("No se pudo leer ese fotograma.")
        actual_index = max(0, round(self._capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
        timestamp = self._capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if not math.isfinite(timestamp) or (timestamp == 0 and actual_index > 0):
            timestamp = actual_index / self.fps
        if (frame.shape[1], frame.shape[0]) != self.resolution:
            raise ValueError("La resolución cambió dentro del video; no se estira la imagen.")
        return VideoFrameSource(frame, self.path, actual_index, timestamp)

    def close(self):
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
