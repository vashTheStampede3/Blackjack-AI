"""Centralized logging with print() redirection and categorization.

Thread-safety notes (this code runs on the Tk thread AND the worker thread):
  * the log deque, the callback list, and the redirector line buffer are all
    guarded by locks
  * callbacks are invoked on the CALLING thread — GUI consumers must marshal
    to the Tk thread themselves (LoggingWindow queues entries and drains them
    from an `after()` loop)
"""

import re
import sys
import threading
import json
import logging
import os
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Tuple


class LogCategory(Enum):
    MODEL_LOADING = "Model Loading"
    CARD_COUNTER = "Card Counter"
    DETECTION = "Detection"
    PERFORMANCE = "Performance"
    ERROR = "Error"
    STARTUP = "Startup"
    UI = "UI"
    GENERAL = "General"


@dataclass
class LogEntry:
    timestamp: datetime
    category: LogCategory
    message: str
    level: str  # "INFO" | "WARNING" | "ERROR"
    event: str | None = None
    action_id: str | None = None


class _SessionHandler(RotatingFileHandler):
    def handleError(self, record):
        # Do not recurse through redirected stderr if the disk cannot be written.
        raise


_PATTERNS = [
    (re.compile(r"error|failed|exception|traceback", re.IGNORECASE), LogCategory.ERROR, "ERROR"),
    (re.compile(r"slow cycle|cycle_ms|performance", re.IGNORECASE), LogCategory.PERFORMANCE, "WARNING"),
    (re.compile(r"count|shoe|counter", re.IGNORECASE), LogCategory.CARD_COUNTER, "INFO"),
    (re.compile(r"model|roboflow|yolo|weights", re.IGNORECASE), LogCategory.MODEL_LOADING, "INFO"),
    (re.compile(r"^P\d+ card|dealer|round|cutting card|table cleared", re.IGNORECASE), LogCategory.DETECTION, "INFO"),
    (re.compile(r"starting|loaded|ready|initialized", re.IGNORECASE), LogCategory.STARTUP, "INFO"),
    (re.compile(r"monitor|window|status", re.IGNORECASE), LogCategory.UI, "INFO"),
]


class LogManager:
    """Singleton store of recent log entries with change callbacks."""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.logs = deque(maxlen=2000)
        self._callbacks = []
        self._lock = threading.Lock()
        self.session_id = None
        self.session_path = None
        self.persistence_error = None
        self._file_handler = None
        self._secret_values = ()
        self._dispatch = threading.local()

    def start_session(self, directory):
        """Keep a bounded, local JSON-lines record independently of the log window."""
        if self._file_handler is not None:
            return self.session_path
        self.session_id = f"{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}"
        self._secret_values = tuple(
            value for name, value in os.environ.items()
            if value and any(part in name.upper()
                            for part in ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD")))
        self._secret_values = tuple(sorted(self._secret_values, key=len, reverse=True))
        try:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            self.session_path = directory / f"session_{self.session_id}.jsonl"
            self._file_handler = _SessionHandler(
                self.session_path, maxBytes=5 * 1024 * 1024, backupCount=2,
                encoding="utf-8")
        except OSError as exc:
            self.persistence_error = self.redact(exc)
            self.add_log(f"No se pudo abrir el archivo de logs: {exc}", level="ERROR",
                         event="LOG_FILE_ERROR")
        return self.session_path

    def redact(self, value):
        text = str(value)
        for secret in self._secret_values:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)(authorization[\s\"']*[:=][\s\"']*)(?:bearer|basic)\s+[^\s\"']+",
                      r"\1[REDACTED]", text)
        text = re.sub(r"(?i)((?:api[_-]?key|access_token|token|password|authorization)"
                      r"[\s\"']*[:=][\s\"']*)([^\s&\"']+)",
                      r"\1[REDACTED]", text)
        text = re.sub(r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{20,})",
                      "[REDACTED]", text)
        return text

    def _redact_details(self, value):
        if isinstance(value, dict):
            return {self.redact(key): ("[REDACTED]" if re.search(
                r"api[_-]?key|token|secret|password|authorization", str(key), re.I)
                else self._redact_details(item)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact_details(item) for item in value]
        return value if value is None or isinstance(value, (bool, int, float)) else self.redact(value)

    def exception(self, message, *, action_id=None, exc_info=None):
        info = exc_info or sys.exc_info()
        detail = "".join(traceback.format_exception(*info)) if info[0] else ""
        self.add_log(f"{message}\n{detail}".rstrip(), level="ERROR",
                     category=LogCategory.ERROR, event="EXCEPTION", action_id=action_id)

    def close_session(self):
        with self._lock:
            if self._file_handler is not None:
                try:
                    self._file_handler.close()
                except Exception as exc:
                    self.persistence_error = self.redact(exc)
                self._file_handler = None

    @staticmethod
    def _categorize(message: str) -> Tuple[LogCategory, str]:
        for pattern, category, level in _PATTERNS:
            if pattern.search(message):
                return category, level
        return LogCategory.GENERAL, "INFO"

    def add_log(self, message: str, level: str | None = None, *, category=None,
                event=None, action_id=None, details=None):
        message = self.redact(message).strip()
        if not message:
            return
        # A plain-print logger folds the level into the text (engine's
        # _level_aware adapter); recover it so entries still color correctly.
        if level is None:
            for prefix in ("WARNING: ", "ERROR: "):
                if message.startswith(prefix):
                    level, message = prefix[:-2], message[len(prefix):]
                    break
        detected_category, detected_level = self._categorize(message)
        entry = LogEntry(datetime.now(), category or detected_category, message,
                         level or detected_level,
                         self.redact(event) if event is not None else None,
                         self.redact(action_id) if action_id is not None else None)
        with self._lock:
            self.logs.append(entry)
            disk_error = None
            if self._file_handler is not None:
                payload = {"timestamp": entry.timestamp.isoformat(timespec="milliseconds"),
                           "session_id": self.session_id,
                           "thread": threading.current_thread().name,
                           "level": entry.level, "category": entry.category.value,
                           "event": self.redact(event) if event is not None else None,
                           "action_id": self.redact(action_id) if action_id is not None else None,
                           "message": message}
                try:
                    if details is not None:
                        try:
                            payload["details"] = self._redact_details(details)
                        except Exception:
                            payload["details"] = "[Unserializable log details]"
                    record = logging.LogRecord("blackjack", logging.INFO, "", 0,
                                               json.dumps(payload, ensure_ascii=False, default=str), (), None)
                    self._file_handler.emit(record)
                except Exception as exc:
                    self.persistence_error = self.redact(exc)
                    try:
                        self._file_handler.close()
                    except Exception:
                        pass  # Preserve the original write failure below.
                    self._file_handler = None
                    disk_error = LogEntry(datetime.now(), LogCategory.ERROR,
                        f"Escritura de logs interrumpida: {self.persistence_error}", "ERROR")
                    self.logs.append(disk_error)
            callbacks = list(self._callbacks)
        # A subscriber may print while handling an entry. Store that print,
        # but do not recursively notify subscribers on the same thread.
        if getattr(self._dispatch, "active", False):
            return
        self._dispatch.active = True
        try:
            self._notify_callbacks(callbacks, entry, disk_error)
        finally:
            self._dispatch.active = False

    def _notify_callbacks(self, callbacks, entry, disk_error):
        for callback in callbacks:
            try:
                callback(entry)
                if disk_error is not None:
                    callback(disk_error)
            except Exception as e:
                self.exception("Error in log callback")
                if sys.__stderr__ is not None:
                    try:
                        sys.__stderr__.write(self.redact(f"Error in log callback: {e}\n"))
                    except Exception:
                        pass

    def register_callback(self, callback: Callable[[LogEntry], None]):
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[LogEntry], None]):
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def get_all_logs(self):
        with self._lock:
            return list(self.logs)

    def clear_logs(self):
        with self._lock:
            self.logs.clear()


class PrintRedirector:
    """Wraps a std stream: console output is preserved, complete lines are
    forwarded to the LogManager. Safe when the original stream is None
    (pythonw.exe) and when multiple threads print concurrently."""

    def __init__(self, log_manager: LogManager, original, level: str | None = None):
        self.log_manager = log_manager
        self.original = original
        self.level = level
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text: str):
        length = len(text)
        lines_to_log = []
        with self._lock:
            self._buffer += text
            if "\n" in self._buffer:
                parts = self._buffer.split("\n")
                self._buffer = parts[-1]
                lines_to_log = [ln for ln in parts[:-1] if ln.strip()]
        for line in lines_to_log:
            self._emit(line)
        return length

    def _emit(self, line):
        # Redact only once the whole line is assembled: print() often writes
        # the label and credential as separate chunks.
        line = self.log_manager.redact(line)
        if self.original is not None:
            try:
                self.original.write(line + "\n")
            except Exception:
                pass
        self.log_manager.add_log(line, level=self.level)

    def flush(self):
        with self._lock:
            pending, self._buffer = self._buffer, ""
        if pending.strip():
            self._emit(pending)
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass

    def isatty(self):
        return bool(self.original is not None and self.original.isatty())


def install_redirectors(log_manager: LogManager):
    """Route stdout and stderr through the log manager (console preserved)."""
    if not isinstance(sys.stdout, PrintRedirector):
        sys.stdout = PrintRedirector(log_manager, sys.stdout)
    if not isinstance(sys.stderr, PrintRedirector):
        sys.stderr = PrintRedirector(log_manager, sys.stderr, level="ERROR")


def install_exception_hooks(log_manager):
    def uncaught(kind, error, tb):
        log_manager.exception("Excepción no controlada", exc_info=(kind, error, tb))

    def worker_error(args):
        log_manager.exception(f"Falló el hilo {args.thread.name}",
                              exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = uncaught
    threading.excepthook = worker_error
