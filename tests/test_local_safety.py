"""Local-only app policy; no network, browser or casino interactions."""
from types import SimpleNamespace

import pytest

from lib.common import constants
from lib.logic import models
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
