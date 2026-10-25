"""Card-detection backends.

Every backend returns predictions in ONE normalized format:
    {"cx": float, "cy": float, "width": float, "height": float,
     "class": str, "confidence": float}
with cx/cy the BOX CENTER in the coordinate space of the input image.
(The Roboflow REST API natively returns centers; ultralytics returns corners —
both are converted here so downstream code never has to care.)
"""

import threading
import json
from pathlib import Path

import cv2
import numpy as np

from ..common import constants

_ONNX_INFERENCE_LOCK = threading.Lock()
cv2.setNumThreads(2)


class ModelError(RuntimeError):
    """Raised when inference fails (network, auth, missing backend...)."""


class LocalYoloModel:
    """Local ultralytics YOLO weights (models/*.pt). Used when available."""

    name = "Local YOLO"
    hosted = False

    def __init__(self, weights_path):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ModelError("ultralytics is not installed (pip install ultralytics)") from e
        self._model = YOLO(str(weights_path))
        try:
            import torch
            self._device = 0 if torch.cuda.is_available() else "cpu"
        except ImportError:
            self._device = "cpu"

    def predict(self, image_bgr: np.ndarray, confidence=50, overlap=45):
        conf = max(0.0, min(1.0, confidence / 100.0))
        iou = max(0.05, min(1.0, overlap / 100.0))
        results = self._model.predict(
            source=image_bgr, conf=conf, iou=iou, imgsz=640,
            verbose=False, device=self._device,
        )
        preds = []
        if not results:
            return preds
        r = results[0]
        names = getattr(r, "names", {})
        for b in r.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            preds.append({
                "cx": (x1 + x2) / 2.0,
                "cy": (y1 + y2) / 2.0,
                "width": x2 - x1,
                "height": y2 - y1,
                "class": str(names.get(int(b.cls.item()), int(b.cls.item()))),
                "confidence": float(b.conf.item()),
            })
        return preds


def letterbox(image_bgr: np.ndarray, size: int = 640, color=(114, 114, 114)):
    """Resize keeping aspect, pad to size x size (YOLO preprocessing).
    Returns (padded, ratio, (pad_x, pad_y))."""
    h, w = image_bgr.shape[:2]
    ratio = min(size / w, h and size / h or 1)
    new_w, new_h = max(1, round(w * ratio)), max(1, round(h * ratio))
    resized = cv2.resize(image_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_x, pad_y = (size - new_w) / 2.0, (size - new_h) / 2.0
    top, bottom = int(round(pad_y - 0.1)), int(round(pad_y + 0.1))
    left, right = int(round(pad_x - 0.1)), int(round(pad_x + 0.1))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                cv2.BORDER_CONSTANT, value=color)
    return padded, ratio, (left, top)


def nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_threshold: float):
    """Greedy non-maximum suppression; returns kept indices."""
    if len(boxes_xyxy) == 0:
        return []
    x1, y1, x2, y2 = boxes_xyxy.T
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        ix1 = np.maximum(x1[i], x1[rest])
        iy1 = np.maximum(y1[i], y1[rest])
        ix2 = np.minimum(x2[i], x2[rest])
        iy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, ix2 - ix1) * np.maximum(0.0, iy2 - iy1)
        iou = inter / np.maximum(1e-9, areas[i] + areas[rest] - inter)
        order = rest[iou <= iou_threshold]
    return keep


class OnnxModel:
    """YOLO weights exported to ONNX (ultralytics: model.export(format='onnx')),
    run with onnxruntime — up to ~3x faster on CPU than the torch path and no
    multi-GB torch dependency on the inference machine."""

    name = "Local ONNX"
    hosted = False

    def __init__(self, model_path):
        try:
            import onnxruntime
        except ImportError as e:
            raise ModelError("onnxruntime is not installed "
                             "(pip install onnxruntime)") from e
        try:
            # Keep local inference light on laptops. This app can run player
            # and dealer models concurrently, so an unrestricted ONNX pool
            # can otherwise oversubscribe the CPU.
            session_options = onnxruntime.SessionOptions()
            session_options.intra_op_num_threads = 2
            session_options.inter_op_num_threads = 1
            session_options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
            self._session = onnxruntime.InferenceSession(
                str(model_path), sess_options=session_options,
                providers=["CPUExecutionProvider"])
        except Exception as e:
            raise ModelError(f"Failed to load ONNX model {model_path}: {e}") from e
        self._input = self._session.get_inputs()[0]
        shape = self._input.shape  # (1, 3, H, W) — H/W may be symbolic
        self._size = int(shape[2]) if isinstance(shape[2], int) else 640
        # ultralytics embeds the class-name dict in the model metadata.
        self._names = {}
        meta = self._session.get_modelmeta().custom_metadata_map
        if "names" in meta:
            try:
                import ast
                self._names = {int(k): str(v) for k, v in
                               ast.literal_eval(meta["names"]).items()}
            except (ValueError, SyntaxError):
                pass

        # Roboflow may export numeric placeholder class names. The authorized
        # artifact sidecar contains the actual class map and preprocessing.
        self._resize_mode = "letterbox"
        sidecar = Path(str(model_path)+".json")
        if sidecar.exists():
            try:
                info = json.loads(sidecar.read_text(encoding="utf-8"))
                names = info.get("names")
                if isinstance(names,list):
                    self._names = dict(enumerate(map(str,names)))
                elif isinstance(names,dict):
                    self._names = {int(k):str(v) for k,v in names.items()}
                preproc = info.get("preprocessing",{})
                if isinstance(preproc,str):
                    preproc = json.loads(preproc)
                self._resize_mode = preproc.get("resize",{}).get("format","letterbox")
            except (OSError,ValueError,TypeError,AttributeError):
                raise ModelError("Invalid local model metadata sidecar") from None

    def predict(self, image_bgr: np.ndarray, confidence=50, overlap=45):
        conf = max(0.0, min(1.0, confidence / 100.0))
        iou = max(0.05, min(1.0, overlap / 100.0))
        h,w = image_bgr.shape[:2]
        if self._resize_mode == "Stretch to":
            padded = cv2.resize(image_bgr,(self._size,self._size))
            ratio = (self._size/w,self._size/h)
            pad_x = pad_y = 0
        else:
            # Roboflow falls back from 'Fit within' to black-edge letterboxing.
            color = (114,114,114) if self._resize_mode == "letterbox" else (0,0,0)
            padded, ratio, (pad_x,pad_y) = letterbox(image_bgr,self._size,color=color)
        blob = padded[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        try:
            with _ONNX_INFERENCE_LOCK:
                out = self._session.run(None, {self._input.name: blob})[0]
        except Exception as e:
            raise ModelError(f"ONNX inference failed: {e}") from e
        return self._decode(out[0], conf, iou, ratio, pad_x, pad_y)

    def _decode(self, output, conf, iou, ratio, pad_x, pad_y):
        """ultralytics YOLOv8+ raw head: (4 + n_classes, n_anchors) with
        cx/cy/w/h in letterboxed pixels."""
        if output.shape[0] < output.shape[1]:
            preds = output  # (4+nc, n)
        else:
            preds = output.T
        boxes = preds[:4].T              # (n, cx cy w h)
        class_scores = preds[4:].T       # (n, nc)
        cls = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(len(cls)), cls]
        if class_scores.shape[1] > 1:
            top_two = np.argpartition(class_scores, -2, axis=1)[:, -2:]
            second_cls = np.where(top_two[:, 0] == cls, top_two[:, 1], top_two[:, 0])
            second_scores = class_scores[np.arange(len(cls)), second_cls]
        else:
            second_cls, second_scores = cls.copy(), np.zeros_like(scores)
        mask = scores >= conf
        boxes, scores, cls = boxes[mask], scores[mask], cls[mask]
        second_cls, second_scores = second_cls[mask], second_scores[mask]
        if not len(boxes):
            return []
        xyxy = np.empty_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
        keep = nms(xyxy, scores, iou)
        results = []
        ratio_x,ratio_y = ratio if isinstance(ratio,tuple) else (ratio,ratio)
        for i in keep:
            cx = (boxes[i, 0] - pad_x) / ratio_x
            cy = (boxes[i, 1] - pad_y) / ratio_y
            results.append({
                "cx": float(cx), "cy": float(cy),
                "width": float(boxes[i, 2] / ratio_x),
                "height": float(boxes[i, 3] / ratio_y),
                "class": self._names.get(int(cls[i]), str(int(cls[i]))),
                "confidence": float(scores[i]),
                "class_margin": float(scores[i]-second_scores[i]),
                "runner_up_class": self._names.get(int(second_cls[i]), str(int(second_cls[i]))),
                "ambiguous": bool(constants.CARD_EVIDENCE_ENABLED and scores[i]-second_scores[i] < .10),
            })
        return results


class RoboflowModel:
    """Hosted Roboflow inference. Downscales uploads and rescales results."""

    name = "Roboflow API"
    hosted = True  # remote session object — does not survive system sleep

    def __init__(self, project_id, model_version):
        import os
        self._key = os.environ.get("ROBOFLOW_API_KEY", "")
        if not self._key:
            raise ModelError("ROBOFLOW_API_KEY is missing; no embedded credential is used")
        self._model_id = f"{project_id}/{int(model_version)}"

    def predict(self, image_bgr: np.ndarray, confidence=50, overlap=45):
        import base64
        import requests
        from .table_privacy import private_table_frame
        try:
            upload, (offset_x, offset_y) = private_table_frame(image_bgr)
        except ValueError as e:
            raise ModelError(str(e)) from None
        h, w = upload.shape[:2]
        scale = 1.0
        if w > constants.API_UPLOAD_MAX_WIDTH:
            scale = constants.API_UPLOAD_MAX_WIDTH / w
            upload = cv2.resize(
                upload,
                (constants.API_UPLOAD_MAX_WIDTH, max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        try:
            ok, encoded = cv2.imencode(".jpg", upload, [cv2.IMWRITE_JPEG_QUALITY,95])
            if not ok:
                raise ModelError("Table ROI encoding failed")
            response = requests.post("https://detect.roboflow.com/"+self._model_id,
                params={"api_key":self._key,"confidence":int(confidence),"overlap":int(overlap)},
                data=base64.b64encode(encoded).decode("ascii"),
                headers={"Content-Type":"application/x-www-form-urlencoded"},
                timeout=(5,20),allow_redirects=False)
            if response.status_code != 200:
                raise ModelError(f"Roboflow HTTP {response.status_code}; credential and response suppressed")
            raw = response.json().get("predictions", [])
        except (requests.RequestException,ValueError):
            raise ModelError("Roboflow request failed; URL and credential suppressed") from None

        inv = 1.0 / scale
        return [
            {
                # Roboflow x/y are already the box center.
                "cx": p["x"] * inv + offset_x,
                "cy": p["y"] * inv + offset_y,
                "width": p["width"] * inv,
                "height": p["height"] * inv,
                "class": str(p["class"]),
                "confidence": float(p.get("confidence", 0.0)),
            }
            for p in raw
        ]


class ManualShuffleModel:
    """Local rank models have no cutting-card class; no remote poll is needed."""
    name = "Shuffle reset: manual"
    hosted = False

    def predict(self, image_bgr, confidence=50, overlap=45):
        return []


class ModelProvider:
    """Process-wide lazy, thread-safe access to the two detection models.

    Nothing touches the network or loads weights until the first predict —
    the GUI starts instantly and model init happens on the worker thread.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._players = None
        self._dealer = None

    @classmethod
    def get(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _build(self, weights_name, project_id, model_version):
        # Preference order: local ONNX (lightest/fastest CPU) -> local .pt
        # (ultralytics) -> hosted API.
        onnx_path = (constants.MODELS_DIR / weights_name).with_suffix(".onnx")
        if onnx_path.exists():
            try:
                return OnnxModel(onnx_path)
            except Exception as e:
                print(f"ONNX weights {onnx_path.name} unusable ({e}); trying .pt.")
        weights = constants.MODELS_DIR / weights_name
        if weights.exists():
            try:
                return LocalYoloModel(weights)
            except Exception as e:
                # Corrupt weights, torch/DLL trouble, version mismatch, ... —
                # report it and fall through to the hosted API.
                print(f"Local weights {weights.name} unusable ({e}).")
        if constants.LOCAL_ONLY_INFERENCE:
            raise ModelError(f"No usable local model for {weights_name}; remote inference is disabled")
        return RoboflowModel(project_id, model_version)

    def players_model(self):
        with self._lock:
            if self._players is None:
                self._players = self._build(
                    "player_cards.pt", constants.PROJECT_ID_PLAYERS, constants.MODEL_VERSION_PLAYERS)
            return self._players

    def dealer_model(self):
        with self._lock:
            if self._dealer is None:
                local_dealer = any((constants.MODELS_DIR/name).exists() for name in ("dealer_cards.pt","dealer_cards.onnx"))
                if self._players is not None and not self._players.hosted and not local_dealer:
                    self._dealer = ManualShuffleModel()
                else:
                    self._dealer = self._build(
                        "dealer_cards.pt", constants.PROJECT_ID_DEALER, constants.MODEL_VERSION_DEALER)
            return self._dealer

    def has_hosted(self):
        """True if any initialized backend is a hosted (network) session."""
        with self._lock:
            return any(getattr(m, "hosted", False)
                       for m in (self._players, self._dealer))

    def refresh(self, log=None):
        """Rebuild hosted model sessions and re-validate local weight files.

        After a long idle (system sleep/restore) the hosted Roboflow session
        is stale; a local weights file can also be corrupted while the app
        idles. Hosted backends are rebuilt unconditionally; local backends
        are reloaded only after their file passes an integrity check (exists,
        > 1 KB, constructable) — a failing file is reported and the slot
        falls through to the next backend instead of crashing.

        Blocking work (network handshake, weight load) happens OUTSIDE the
        lock; each rebuilt model is swapped in atomically. `log` may accept
        the engine logger's level= keyword.
        """
        if log is None:
            log = lambda message, level=None: print(
                f"{level}: {message}" if level and level != "INFO" else message)
        for attr, weights_name, project_id, model_version in (
                ("_players", "player_cards.pt",
                 constants.PROJECT_ID_PLAYERS, constants.MODEL_VERSION_PLAYERS),
                ("_dealer", "dealer_cards.pt",
                 constants.PROJECT_ID_DEALER, constants.MODEL_VERSION_DEALER)):
            with self._lock:
                model = getattr(self, attr)
            if model is None:
                continue  # never built — the lazy getter handles first use
            fresh = self._refresh_one(model, weights_name, project_id,
                                      model_version, log)
            with self._lock:
                setattr(self, attr, fresh)

    def _refresh_one(self, model, weights_name, project_id, model_version, log):
        if isinstance(model, ManualShuffleModel):
            return model
        if getattr(model, "hosted", False):
            # Rebuild from scratch via _build: re-checks local weights first,
            # same preference order as startup.
            return self._build(weights_name, project_id, model_version)
        path = constants.MODELS_DIR / weights_name
        if isinstance(model, OnnxModel):
            path = path.with_suffix(".onnx")
        try:
            size = path.stat().st_size if path.exists() else -1
            if size <= 1024:  # a real weights file is megabytes, not bytes
                raise ModelError("file is missing" if size < 0
                                 else f"file is only {size} bytes")
            return type(model)(path)
        except Exception as e:
            log(f"Local weights {path.name} failed validation ({e}); "
                "falling back to the next backend.", level="ERROR")
            return self._build(weights_name, project_id, model_version)

    @property
    def backend_name(self):
        parts = []
        if self._players is not None:
            parts.append(self._players.name)
        if self._dealer is not None and (not parts or self._dealer.name != parts[0]):
            parts.append(self._dealer.name)
        return " + ".join(parts) if parts else "not loaded"
