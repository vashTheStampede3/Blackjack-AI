"""Record app actions and observed outcomes, including controls in child dialogs.

Instrumentation is scoped to roots with a UIActionLogger. It never records
keystrokes, entry contents, screenshots, or activity in other applications.
"""
from functools import wraps
import itertools
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

from ..logic.log_manager import LogCategory


def _observe(callback, default=None):
    """Diagnostics must never change whether an application callback runs."""
    try:
        return callback()
    except Exception:
        return default


def window_visibility_state(*, exists, viewable=False, mapped=False, error=False, attempts_left=0):
    """Pure final-state resolver used by deferred dialog visibility checks."""
    if error:
        return "error"
    if not exists:
        return "closed"
    if viewable or mapped:
        return "visible"
    return "pending" if attempts_left > 0 else "hidden"


class UIActionLogger:
    def __init__(self, root, log_manager):
        self.root, self.log = root, log_manager
        self._ids = itertools.count(1)
        self.current = None
        self._last_exception = None
        self._last_status = None
        self.install_error = None
        root._ui_action_logger = self
        root.report_callback_exception = self.report_callback_exception
        try:
            _install_callbacks()
            root.bind_all("<ButtonPress-1>", self._disabled_click, add="+")
        except Exception as exc:
            self.install_error = _observe(lambda: self.log.redact(exc), "Error al activar el registro")
            _observe(lambda: self.log.exception("No se pudo activar el seguimiento de botones.",
                                               exc_info=sys.exc_info()))
        else:
            self.record("UI_LOGGING_READY", "Seguimiento de botones activado para esta ventana y sus diálogos.")

    @property
    def current_id(self):
        return self.current["id"] if self.current else None

    def record(self, event, message, *, action_id=None, level="INFO", details=None):
        _observe(lambda: self.log.add_log(message, level=level, category=LogCategory.UI,
                         event=event, action_id=action_id or self.current_id, details=details))

    def context(self):
        return _observe(self._context, {"observation_unavailable": True})

    def _context(self):
        app = self.root
        controller = getattr(app, "controller", None)
        engine = getattr(controller, "engine", None)
        source = getattr(app, "monitor", None)
        try:
            if not app.winfo_exists():
                return {"app_closed": True}
        except tk.TclError:
            return {"app_closed": True}
        context = {"worker": getattr(controller, "state", None),
                   "source_kind": app.source_kind.get() if hasattr(app, "source_kind") else None,
                   "selected_resolution": [source.width, source.height] if source else None,
                   "profile": app.profile_var.get() if hasattr(app, "profile_var") else None}
        if engine:
            context.update(round=engine.round_number, cards_seen=engine.counter.cards_seen,
                           running_count=engine.counter.running_count)
            try:
                context["engine_resolution"] = list(engine.capture.resolution)
            except ValueError:
                context["engine_resolution"] = None
        overlay = getattr(app, "round_overlay", None)
        context["overlay_enabled"] = bool(overlay and overlay.enabled)
        context["overlay_menu_card"] = getattr(overlay, "_selected_id", None)
        context["overlay_editing"] = getattr(overlay, "editing", False)
        return context

    def _windows(self):
        found = {}
        def walk(owner):
            for child in owner.winfo_children():
                if isinstance(child, tk.Toplevel):
                    found[str(child)] = child
                walk(child)
        try:
            walk(self.root)
        except tk.TclError:
            pass
        return found

    @staticmethod
    def _label(widget, func, target=None):
        label = getattr(widget, "_ui_action_label", None)
        if not label:
            try:
                # Combobox accepts 'text' as an abbreviation of textvariable;
                # that yields PY_VAR1 instead of an identifiable control.
                label = "Selector" if isinstance(widget, ttk.Combobox) else widget.cget("text")
            except tk.TclError:
                label = ""
        callback = getattr(func, "__name__", type(func).__name__)
        return f"{label or widget.winfo_class()} ({callback})" + (f" · {target}" if target else "")

    def wrap(self, widget, func, target=None):
        if getattr(func, "_ui_action_logged", False):
            return func

        @wraps(func)
        def run(*args, **kwargs):
            parent = self.current
            action = {"id": f"UI-{next(self._ids):05d}", "outcome": None,
                      "label": _observe(lambda: self._label(widget, func, target), "control")}
            self.current = action
            started = time.monotonic()
            before = _observe(self.context, {})
            windows = _observe(self._windows, {})
            self.record("ACTION_START", f"INICIO · {action['label']}",
                        details={"widget": str(widget), "before": before,
                                 "window": _observe(lambda: str(widget.winfo_toplevel())),
                                 "parent_action": parent["id"] if parent else None})
            try:
                result = func(*args, **kwargs)
                after = _observe(self.context, {})
                observed_windows = _observe(self._windows, {})
                new_windows = {k: v for k, v in observed_windows.items() if k not in windows}
                outcome = action["outcome"]
                if outcome is None and (result is False or _observe(lambda: getattr(result, "ok", True), True) is False):
                    outcome = "RECHAZADO"
                if new_windows and outcome not in ("ERROR", "CANCELADO", "BLOQUEADO", "RECHAZADO"):
                    outcome = "PENDIENTE"
                    _observe(lambda: self._watch_windows(action, new_windows))
                if outcome in (None, "INFORMADO"):
                    closed = set(windows) - set(observed_windows)
                    outcome = ("VENTANA_CERRADA" if closed else "CAMBIO_OBSERVADO" if
                               before and after and "observation_unavailable" not in before
                               and "observation_unavailable" not in after and before != after
                               else outcome or "SIN_CONFIRMAR")
                explanation = (" · terminó sin un resultado comprobable"
                               if outcome == "SIN_CONFIRMAR" else "")
                self.record("ACTION_RETURN", f"{outcome} · {action['label']}{explanation}",
                            level="WARNING" if outcome in ("RECHAZADO", "SIN_CONFIRMAR") else
                                  "ERROR" if outcome == "ERROR" else "INFO",
                            details={"duration_ms": round((time.monotonic()-started)*1000, 1),
                                     "after": after, "callback_returned": True,
                                     "changed_fields": [key for key in after if before.get(key) != after[key]]})
                return result
            except Exception as exc:
                self._last_exception = exc
                _observe(lambda: self.log.exception(f"ERROR · {action['label']}", action_id=action["id"],
                                                   exc_info=sys.exc_info()))
                raise
            finally:
                self.current = parent
        run._ui_action_logged = True
        return run

    def _watch_windows(self, action, windows):
        """Wait for actual map events before recording a new dialog outcome."""
        for name in windows:
            self.record("WINDOW_CREATED", f"CREATED · window {name}",
                        action_id=action["id"], details={"window": name, "state": "created"})
        observed = {name: {"mapped": False, "closed": False, "error": False}
                    for name in windows}
        for name, window in windows.items():
            def mapped(_event, name=name):
                observed[name]["mapped"] = True
            def unmapped(_event, name=name, window=window):
                try:
                    if not window.winfo_exists():
                        observed[name]["closed"] = True
                except tk.TclError:
                    observed[name]["closed"] = True
            try:
                window.bind("<Map>", mapped, add="+")
                window.bind("<Unmap>", unmapped, add="+")
                observed[name]["mapped"] = bool(window.winfo_viewable())
            except tk.TclError:
                observed[name]["error"] = True
        self._confirm_windows(action, windows, observed, attempts=30)

    def _confirm_windows(self, action, windows, observed=None, attempts=0):
        observed = observed or {}
        pending = False
        for name, window in windows.items():
            try:
                exists = bool(window.winfo_exists())
                state = window_visibility_state(
                    exists=exists, viewable=bool(exists and window.winfo_viewable()),
                    mapped=observed.get(name, {}).get("mapped", False),
                    error=observed.get(name, {}).get("error", False), attempts_left=attempts)
                pending |= state == "pending"
            except tk.TclError:
                state = "error"
            if state != "pending":
                self.record("WINDOW_VISIBLE" if state == "visible" else "WINDOW_NOT_VISIBLE",
                            f"{'CONFIRMADO' if state == 'visible' else state.upper()} · window {name}",
                            action_id=action["id"],
                            level="INFO" if state == "visible" else "WARNING",
                            details={"window": name, "state": state})
        if pending and attempts > 0:
            _observe(lambda: self.root.after(40, lambda: self._confirm_windows(
                action, windows, observed, attempts - 1)))

    def status(self, message, error=False):
        if self.current:
            self.current["outcome"] = "ERROR" if error else self.current["outcome"] or "INFORMADO"
        key = (message, error, self.current_id)
        if key == self._last_status:
            return
        self._last_status = key
        if error and sys.exc_info()[0] is not None:
            _observe(lambda: self.log.exception(message, action_id=self.current_id, exc_info=sys.exc_info()))
        else:
            self.record("STATUS", message, level="ERROR" if error else "INFO")

    def blocked(self, message):
        if self.current:
            self.current["outcome"] = "BLOQUEADO"
        self.record("ACTION_BLOCKED", f"BLOQUEADO · {message}", level="WARNING")
        self.root.set_status(message)

    def pending(self, message):
        if self.current:
            self.current["outcome"] = "PENDIENTE"
        self.record("ACTION_PENDING", message)
        return self.current_id

    def cancelled(self, message):
        if self.current:
            self.current["outcome"] = "CANCELADO"
        self.record("ACTION_CANCELLED", message)

    def complete(self, action_id, message, *, error=False):
        if self.current and self.current_id == action_id:
            self.current["outcome"] = "ERROR" if error else "CONFIRMADO"
        self.record("ACTION_FAILED" if error else "ACTION_CONFIRMED",
                    f"{'ERROR' if error else 'CONFIRMADO'} · {message}",
                    action_id=action_id, level="ERROR" if error else "INFO")

    def report_callback_exception(self, kind, error, tb):
        if error is not self._last_exception:
            _observe(lambda: self.log.exception("Error en un callback de la interfaz", exc_info=(kind, error, tb)))
        self._last_exception = None
        text = _observe(lambda: self.log.redact(error), "Error en la interfaz")
        if hasattr(self.root, "status_var"):
            _observe(lambda: self.root.status_var.set(f"Error: {text}"))
        _observe(lambda: messagebox.showerror("La acción falló", f"{text}\n\nDetalle en: {self.log.session_path}",
                                             parent=self.root))

    def _disabled_click(self, event):
        try:
            widget = event.widget
            if isinstance(widget, (tk.Button, tk.Checkbutton, tk.Radiobutton,
                                   ttk.Button, ttk.Checkbutton, ttk.Radiobutton)):
                disabled = (widget.instate(["disabled"]) if isinstance(widget, ttk.Widget)
                            else str(widget.cget("state")) == "disabled")
                if disabled:
                    self.record("ACTION_BLOCKED", f"BLOQUEADO · {widget.cget('text')} · control deshabilitado",
                                action_id=f"UI-{next(self._ids):05d}", level="WARNING")
        except Exception:
            pass


def _logger(widget):
    return _observe(lambda: getattr(widget._root(), "_ui_action_logger", None))


def _install_callbacks():
    """Include future dialogs and Canvas card controls without tracking mouse movement."""
    if getattr(tk.Misc, "_ui_logging_installed", False):
        return
    register, bind, show = tk.Misc._register, tk.Misc._bind, messagebox._show
    button_types = (tk.Button, tk.Checkbutton, tk.Radiobutton, tk.Menu,
                    ttk.Button, ttk.Checkbutton, ttk.Radiobutton)

    def logged_register(widget, func, subst=None, needcleanup=1):
        audit = _logger(widget)
        # Tk.after renames its wrapper to the scheduled callback's name.
        # The code object's name stays 'callit'; exclude that wrapper so
        # polling/capture timers never appear as user actions in the logs.
        is_timer = getattr(getattr(func, "__code__", None), "co_name", None) == "callit"
        if audit and callable(func) and subst is None and not is_timer and (
                isinstance(widget, button_types + (tk.Tk, tk.Toplevel))):
            func = _observe(lambda: audit.wrap(widget, func), func)
        return register(widget, func, subst, needcleanup)

    def logged_bind(widget, what, sequence, func, add, needcleanup=1):
        audit = _logger(widget)
        if audit and callable(func) and sequence in (
                "<Button-1>", "<ButtonRelease-1>", "<Return>", "<<ComboboxSelected>>"):
            target = str(what[-1]) if len(what) > 2 else sequence
            func = _observe(lambda: audit.wrap(widget, func, target), func)
        return bind(widget, what, sequence, func, add, needcleanup)

    def logged_show(*args, **kwargs):
        owner = kwargs.get("parent") or getattr(tk, "_default_root", None)
        audit = _logger(owner) if owner is not None else None
        icon = kwargs.get("_icon", args[2] if len(args) > 2 else None)
        if audit:
            audit.record("DIALOG_OPEN", "Diálogo abierto",
                         level="ERROR" if icon == "error" else "INFO")
            if icon == "error" and audit.current:
                audit.current["outcome"] = "ERROR"
        result = show(*args, **kwargs)
        if audit:
            audit.record("DIALOG_RESULT", f"Diálogo cerrado · {result}")
            if result in ("no", "cancel") and audit.current:
                audit.current["outcome"] = "CANCELADO"
        return result

    tk.Misc._register, tk.Misc._bind = logged_register, logged_bind
    messagebox._show = logged_show
    tk.Misc._ui_logging_installed = True
