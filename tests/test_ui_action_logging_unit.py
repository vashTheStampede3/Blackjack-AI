"""Headless unit contracts for action auditing; never constructs a Tk interpreter."""
import traceback
from types import SimpleNamespace

import pytest

from lib.interfaces import ui_actions as actions


class FakeRoot:
    def bind_all(self, *args, **kwargs):
        pass

    def winfo_exists(self):
        return True

    def winfo_children(self):
        return []


class FakeButton:
    def __init__(self, root):
        self.root = root

    def _root(self):
        return self.root

    def cget(self, key):
        return "Apply"

    def winfo_toplevel(self):
        return self.root

    def __str__(self):
        return ".apply"


class RecordingLog:
    def __init__(self):
        self.entries = []

    def add_log(self, message, **fields):
        self.entries.append(dict(message=message, **fields))

    def exception(self, message, **fields):
        fields["traceback"] = "".join(traceback.format_exception(*fields["exc_info"]))
        self.entries.append(dict(message=message, event="EXCEPTION", **fields))


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setattr(actions, "_install_callbacks", lambda: None)
    log = RecordingLog()
    root = FakeRoot()
    logger = actions.UIActionLogger(root, log)
    log.entries.clear()
    return logger, root, log


def test_button_return_is_preserved_and_actions_share_id(audit):
    logger, root, log = audit
    sentinel = object()
    wrapped = logger.wrap(FakeButton(root), lambda: sentinel)
    assert wrapped() is sentinel
    assert [e["event"] for e in log.entries] == ["ACTION_START", "ACTION_RETURN"]
    assert log.entries[0]["action_id"] == log.entries[1]["action_id"]
    assert log.entries[0]["action_id"]
    assert "SIN_CONFIRMAR" in log.entries[-1]["message"]
    assert "CONFIRMADO" not in log.entries[-1]["message"]
    assert logger.current is None


def test_callback_exception_is_preserved_with_traceback(audit):
    logger, root, log = audit
    failure = ValueError("synthetic callback failure")

    def fail():
        raise failure

    with pytest.raises(ValueError) as caught:
        logger.wrap(FakeButton(root), fail)()
    assert caught.value is failure
    error = log.entries[-1]
    assert error["event"] == "EXCEPTION"
    assert "Traceback" in error["traceback"]
    assert "synthetic callback failure" in error["traceback"]
    assert error["action_id"] == log.entries[0]["action_id"]
    assert not any(e["event"] == "ACTION_RETURN" for e in log.entries)
    assert logger.current is None


def test_diagnostic_failures_do_not_block_callback(audit, monkeypatch):
    logger, root, log = audit

    def broken(*args, **kwargs):
        raise RuntimeError("diagnostic unavailable")

    monkeypatch.setattr(logger, "_context", broken)
    monkeypatch.setattr(logger, "_windows", broken)
    monkeypatch.setattr(log, "add_log", broken)
    calls = []
    assert logger.wrap(FakeButton(root), lambda: calls.append("ran") or 42)() == 42
    assert calls == ["ran"]
    assert logger.current is None


def test_closing_dialog_reports_window_closed_without_generic_success(audit, monkeypatch):
    logger, root, log = audit
    snapshots = iter([{ ".dialog": object()}, {}])
    monkeypatch.setattr(logger, "_windows", lambda: next(snapshots))
    logger.wrap(FakeButton(root), lambda: None)()
    result = log.entries[-1]
    assert "VENTANA_CERRADA" in result["message"]
    assert "CONFIRMADO" not in result["message"]


def test_renamed_tk_after_wrapper_is_not_instrumented(monkeypatch):
    captured = []
    monkeypatch.setattr(actions.tk.Misc, "_ui_logging_installed", False, raising=False)
    monkeypatch.setattr(actions.tk.Misc, "_register", lambda widget, func, *args: captured.append(func))
    monkeypatch.setattr(actions.tk.Misc, "_bind", actions.tk.Misc._bind)
    monkeypatch.setattr(actions.messagebox, "_show", actions.messagebox._show)
    monkeypatch.setattr(actions.tk, "Button", FakeButton)
    actions._install_callbacks()
    root = FakeRoot()
    log = RecordingLog()
    logger = actions.UIActionLogger(root, log)
    log.entries.clear()
    calls = []

    def callit():
        calls.append("timer")

    callit.__name__ = "_drain"
    assert callit.__code__.co_name == "callit"
    button = FakeButton(root)
    actions.tk.Misc._register(button, callit)
    assert captured[-1] is callit
    captured[-1]()
    assert calls == ["timer"]
    assert log.entries == []
    actions.tk.Misc._register(button, lambda: calls.append("button"))
    captured[-1]()
    assert calls == ["timer", "button"]
    assert [entry["event"] for entry in log.entries] == ["ACTION_START", "ACTION_RETURN"]
