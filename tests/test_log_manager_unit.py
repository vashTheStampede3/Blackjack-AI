"""Isolated local persistence and logging failure contracts."""
import io
import json
import os
import sys

import pytest

from lib.logic.log_manager import LogCategory, LogManager, PrintRedirector


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(LogManager, "_instance", None)
    # Never collect real process credentials into these synthetic tests.
    monkeypatch.setattr(os, "environ", {"SYNTHETIC_API_KEY": "unit-secret-abc123"})
    instance = LogManager()
    yield instance
    instance.close_session()


def read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_jsonl_persists_action_fields_and_nested_redaction(manager, tmp_path):
    path = manager.start_session(tmp_path)
    manager.add_log("request unit-secret-abc123", category=LogCategory.UI,
                    event="ACTION_START", action_id="UI-00001",
                    details={"password": "synthetic-private", "items": ["unit-secret-abc123", 7]})
    manager.close_session()
    records = read_records(path)
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "ACTION_START"
    assert record["action_id"] == "UI-00001"
    assert record["category"] == "UI"
    assert record["timestamp"] and record["session_id"]
    assert record["details"] == {"password": "[REDACTED]", "items": ["[REDACTED]", 7]}
    assert "unit-secret-abc123" not in path.read_text(encoding="utf-8")
    assert "unit-secret-abc123" not in manager.get_all_logs()[0].message


def test_recursive_subscriber_stores_nested_message_without_recursive_dispatch(manager):
    notified = []

    def subscriber(entry):
        notified.append(entry.message)
        manager.add_log("nested message")

    manager.register_callback(subscriber)
    manager.add_log("outer message")
    assert notified == ["outer message"]
    assert [entry.message for entry in manager.get_all_logs()] == ["outer message", "nested message"]


def test_subscriber_failure_is_recorded_and_other_subscribers_continue(manager, monkeypatch):
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stderr)
    received = []

    def failing(entry):
        raise RuntimeError("subscriber unit-secret-abc123")

    manager._secret_values = ("unit-secret-abc123",)
    manager.register_callback(failing)
    manager.register_callback(lambda entry: received.append(entry.message))
    manager.add_log("original")
    assert received == ["original"]
    errors = [entry for entry in manager.get_all_logs() if entry.event == "EXCEPTION"]
    assert len(errors) == 1
    assert "Traceback" in errors[0].message
    assert "unit-secret-abc123" not in errors[0].message + stderr.getvalue()


def test_disk_write_failure_is_visible_in_memory_and_future_logging_works(manager, tmp_path, monkeypatch):
    manager.start_session(tmp_path)
    handler = manager._file_handler

    def disk_full(record):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(handler, "emit", disk_full)
    received = []
    manager.register_callback(received.append)
    manager.add_log("first")
    manager.add_log("after disk failure")
    assert "synthetic disk full" in manager.persistence_error
    assert manager._file_handler is None
    assert any(entry.level == "ERROR" and "synthetic disk full" in entry.message
               for entry in manager.get_all_logs())
    assert manager.get_all_logs()[-1].message == "after disk failure"
    assert any(entry.level == "ERROR" for entry in received)


def test_session_open_failure_is_visible_without_preventing_memory_logs(manager, tmp_path):
    blocked = tmp_path / "file-instead-of-directory"
    blocked.write_text("synthetic", encoding="utf-8")
    manager.start_session(blocked)
    manager.add_log("still usable")
    assert manager.persistence_error
    assert any(entry.event == "LOG_FILE_ERROR" for entry in manager.get_all_logs())
    assert manager.get_all_logs()[-1].message == "still usable"


def test_redirector_redacts_secret_split_across_writes_and_flushes_partial_line(manager, tmp_path):
    manager.start_session(tmp_path)
    original = io.StringIO()
    redirector = PrintRedirector(manager, original)
    redirector.write("credential: unit-secret-")
    assert original.getvalue() == ""
    redirector.write("abc123\npartial")
    redirector.flush()
    assert original.getvalue() == "credential: [REDACTED]\npartial\n"
    assert [entry.message for entry in manager.get_all_logs()] == ["credential: [REDACTED]", "partial"]
