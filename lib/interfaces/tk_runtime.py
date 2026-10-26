"""Resolve bundled Tcl/Tk through the real Python prefix, including uv links."""
import os
from pathlib import Path
import sys


def configure_tk_runtime(base_prefix=None, environ=None):
    try:
        import _tkinter
    except ImportError:
        return {}
    env = os.environ if environ is None else environ
    prefix = Path(base_prefix or sys.base_prefix).resolve()
    configured = {}
    for name, directory, entry in (
            ("TCL_LIBRARY", f"tcl{_tkinter.TCL_VERSION}", "init.tcl"),
            ("TK_LIBRARY", f"tk{_tkinter.TK_VERSION}", "tk.tcl")):
        current = env.get(name)
        if current and (Path(current)/entry).is_file():
            continue
        candidate = prefix/"tcl"/directory
        if (candidate/entry).is_file():
            env[name] = str(candidate)
            configured[name] = str(candidate)
    return configured
