"""Local-only app policy; no network, browser or casino interactions."""
from types import SimpleNamespace

import numpy as np
import pytest

from lib.common import constants
from lib.logic import models, training_data, vision_assist
from lib.logic.executor import Executor


def test_missing_local_weights_never_construct_hosted_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(models, "RoboflowModel", lambda *a: pytest.fail("hosted fallback"))
    with pytest.raises(models.ModelError, match="local"):
        models.ModelProvider().players_model()


def test_app_disabled_executor_cannot_arm_or_confirm(monkeypatch):
    monkeypatch.setitem(constants.EXECUTOR, "mode", "assist")
    calls = []
    dispatcher = SimpleNamespace(dispatch=lambda *a, **k: calls.append((a, k)))
    executor = Executor(snapshot_fn=lambda: {}, monitor_fn=lambda: None,
                        dispatcher=dispatcher, running_fn=lambda: True,
                        log=lambda *a, **k: None, allowed=False)
    assert executor.mode == "off"
    executor.set_mode("assist")
    assert executor.mode == "off"
    assert not executor.arm()
    executor.confirm()
    assert not calls


def test_vision_assist_cannot_send_even_when_enabled(monkeypatch):
    monkeypatch.setitem(constants.VISION, "enabled", True)
    monkeypatch.setitem(constants.VISION, "api_key", "test-only")
    assert not vision_assist.available()
    with pytest.raises(RuntimeError, match="local"):
        vision_assist._post("image", "prompt")


def test_direct_roboflow_inference_cannot_send(monkeypatch):
    monkeypatch.setenv("ROBOFLOW_API_KEY", "test-only")
    model = models.RoboflowModel("sample", 1)
    with pytest.raises(models.ModelError, match="local"):
        model.predict(np.zeros((10, 10, 3), dtype=np.uint8))


def test_training_upload_cannot_send(tmp_path):
    with pytest.raises(RuntimeError, match="local"):
        training_data.upload_batch(root=tmp_path)
