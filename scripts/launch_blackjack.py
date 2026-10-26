"""Desktop entry wrapper. Run with this project's .venv312 Python.

The official main.py owns application startup; this module never starts
detection. Windows keeps a named mutex handle until the application exits.
Unexpected failures are appended to logs/startup_errors.log, with a fallback
under LOCALAPPDATA/Blackjack-AI if the project log directory is unwritable.
"""
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import runpy
import sys
import traceback


WINDOWS = sys.platform == "win32"


def project_root(launcher_file=None):
    return Path(launcher_file or __file__).resolve().parent.parent


def validate_installation(root):
    """Fail before importing the app if its entry point or venv is missing."""
    root = Path(root).resolve()
    for relative in ("main.py", ".venv312/Scripts/python.exe", ".venv312/Scripts/pythonw.exe"):
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Missing required startup file: {path}")
    allowed = [root / ".venv312/Scripts" / name for name in ("python.exe", "pythonw.exe")]
    if Path(sys.executable).resolve() not in [path.resolve() for path in allowed]:
        raise RuntimeError(f"Use the project virtual environment: {allowed[0]}")
    return root / "main.py"


def mutex_name(root):
    normalized = os.path.normcase(str(Path(root).resolve()))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return "Local\\BlackjackAI_" + digest


def _windows_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    return kernel


@contextmanager
def single_instance(root):
    """A session-local, per-installation mutex; Windows cleans up on crashes."""
    if not WINDOWS:
        yield True
        return
    kernel = _windows_api()
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, mutex_name(root))
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    try:
        yield error != 183  # ERROR_ALREADY_EXISTS: leave the existing app alone
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def run_application(root, entry):
    """Preserve the official entry point's __main__ behavior and import paths."""
    previous_cwd, previous_path = Path.cwd(), list(sys.path)
    try:
        os.chdir(root)
        sys.path.insert(0, str(root))
        runpy.run_path(str(entry), run_name="__main__")
    finally:
        sys.path[:] = previous_path
        os.chdir(previous_cwd)


def log_startup_error(root, error):
    """Append the original traceback; return the actual destination used."""
    candidates = [Path(root) / "logs" / "startup_errors.log"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Blackjack-AI" / "startup_errors.log")
    detail = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    record = (f"\n[{datetime.now(timezone.utc).isoformat()}] Blackjack AI startup failure\n"
              f"Project: {root}\nPython: {sys.executable}\n{detail}")
    last_error = None
    for destination in candidates:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("a", encoding="utf-8") as stream:
                stream.write(record)
            return destination
        except OSError as exc:
            last_error = exc
    raise OSError("Unable to write startup log: " + ", ".join(map(str, candidates))) from last_error


def show_error(message):
    """Native error notice for pythonw; no Tk window or app instance is created."""
    if WINDOWS:
        ctypes.windll.user32.MessageBoxW(None, message, "Blackjack AI - startup error", 0x10)


def main(root=None):
    root = project_root() if root is None else Path(root).resolve()
    # main.py installs its own logging hooks. Keep the original console stream
    # so diagnostics still show the traceback after those hooks/session close.
    console = sys.stderr
    try:
        entry = validate_installation(root)
        with single_instance(root) as first:
            if not first:
                return 0
            run_application(root, entry)
        return 0
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code in (None, 0):
            return 0
        try:
            destination = log_startup_error(root, error)
            message = f"Blackjack AI could not start.\n{error}\n\nStartup log:\n{destination}"
        except OSError as log_error:
            message = f"Blackjack AI could not start.\n{error}\n\n{log_error}"
        if console is not None:
            traceback.print_exception(type(error), error, error.__traceback__, file=console)
            console.write(message + "\n")
            console.flush()
        else:
            show_error(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
