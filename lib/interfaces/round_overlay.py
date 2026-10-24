"""Additional compact round panel; existing HUD and main window stay intact."""
import ctypes
from ctypes import wintypes as W
import sys
import tkinter as tk

from ..common import constants
from ..logic.round_observation import overlay_text


def win_api():
    u = ctypes.WinDLL("user32", use_last_error=True)
    for name, args, result in [
        ("GetAncestor", [W.HWND, W.UINT], W.HWND),
        ("GetWindowLongPtrW", [W.HWND, ctypes.c_int], ctypes.c_ssize_t),
        ("SetWindowLongPtrW", [W.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
        ("SetWindowPos", [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT], W.BOOL),
        ("SetWindowDisplayAffinity", [W.HWND, W.DWORD], W.BOOL),
        ("ShowWindow", [W.HWND, ctypes.c_int], W.BOOL),
        ("GetWindowRect", [W.HWND, ctypes.POINTER(W.RECT)], W.BOOL),
    ]:
        fn = getattr(u, name)
        fn.argtypes, fn.restype = args, result
    return u


class RoundOverlay(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        self.title("Blackjack AI — Cartel de ronda")
        self.configure(bg="#10151b", padx=10, pady=6)
        self.attributes("-topmost", True)
        self.opacity = float(constants.UI.get("round_opacity", .85))
        self.geometry("+60+60")
        initial_position = (int(constants.UI.get("round_x", 60)), int(constants.UI.get("round_y", 60)))
        self.count_label = tk.Label(self, bg="#10151b", fg="#f2fff7",
                                   font=(constants.FONT_FAMILY, 17, "bold"))
        self.count_label.pack(anchor="w")
        self.action_label = tk.Label(self, bg="#10151b", fg="#f2fff7", justify="left",
                                    font=(constants.FONT_FAMILY, 19, "bold"))
        self.action_label.pack(anchor="w")
        self.editing = False
        self.enabled = True
        self._capture_hidden = False
        self._state = "warning"
        self._last_alpha = None
        self.affinity_ok = False
        self.hwnd = None
        self._drag_origin = None
        for widget in (self, self.count_label, self.action_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", lambda e: self.remember_position())
        self.update_idletasks()
        if sys.platform == "win32":
            self.api = win_api()
            self.hwnd = self.api.GetAncestor(self.winfo_id(), 2)
            self.apply_styles()
            self.affinity_ok = bool(self.api.SetWindowDisplayAffinity(self.hwnd, 0x11))
        self.move_to(*initial_position)
        self._show_noactivate()
        self._topmost_job = self.after(1000, self._keep_topmost)

    def apply_styles(self):
        if not self.hwnd:
            return
        style = self.api.GetWindowLongPtrW(self.hwnd, -20)
        style |= 0x08000000 | 0x00000080 | 0x00080000  # noactivate/tool/layered
        style = style & ~0x20 if self.editing else style | 0x20
        self.api.SetWindowLongPtrW(self.hwnd, -20, style)
        self.api.SetWindowPos(self.hwnd, W.HWND(-1), 0, 0, 0, 0, 0x33)

    def _show_noactivate(self):
        if not self.enabled or self._capture_hidden:
            return
        # wm deiconify can activate the window; native SW_SHOWNOACTIVATE cannot.
        if self.hwnd:
            self.api.ShowWindow(self.hwnd, 4)
            self.api.SetWindowPos(self.hwnd, W.HWND(-1), 0, 0, 0, 0, 0x13)
        else:
            self.deiconify()

    def _keep_topmost(self):
        if not self.winfo_exists():
            return
        if self.enabled and not self._capture_hidden:
            self._show_noactivate()
        self._topmost_job = self.after(1000, self._keep_topmost)

    def set_editing(self, enabled):
        self.editing = bool(enabled)
        self.apply_styles()
        self._apply_opacity()

    def _drag_start(self, event):
        if self.editing:
            self._drag_origin = (event.x_root-self.winfo_x(), event.y_root-self.winfo_y())

    def _drag_move(self, event):
        if self.editing and self._drag_origin:
            x, y = event.x_root-self._drag_origin[0], event.y_root-self._drag_origin[1]
            self.move_to(x, y)

    def move_to(self, x, y):
        if self.hwnd:
            self.api.SetWindowPos(self.hwnd, W.HWND(-1), int(x), int(y), 0, 0, 0x11)
        else:
            self.geometry(f"{int(x):+d}{int(y):+d}")

    def remember_position(self):
        rect = W.RECT()
        if self.hwnd and self.api.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            constants.UI.update(round_x=rect.left, round_y=rect.top)
        else:
            constants.UI.update(round_x=self.winfo_x(), round_y=self.winfo_y())

    def set_opacity(self, opacity):
        self.opacity = max(.25, min(1.0, float(opacity)))
        constants.UI["round_opacity"] = self.opacity
        self._apply_opacity()

    def _apply_opacity(self):
        alpha = min(self.opacity, .30) if self._state == "idle" and not self.editing else self.opacity
        if alpha != self._last_alpha:
            self.attributes("-alpha", alpha)
            self.apply_styles()
            self._last_alpha = alpha

    def set_capture_hidden(self, hidden):
        self._capture_hidden = hidden
        if hidden:
            if self.hwnd:
                self.api.ShowWindow(self.hwnd, 0)
            else:
                self.withdraw()
        else:
            self._show_noactivate()
        self.update_idletasks()
        if sys.platform == "win32":
            ctypes.windll.dwmapi.DwmFlush()

    def update_from_snapshot(self, snap, running):
        self._state, count, action = overlay_text(snap, running)
        self.count_label.config(text=count)
        self.action_label.config(text=action, fg="#ffc46b" if self._state == "warning" else "#f2fff7")
        self._apply_opacity()

    def close(self):
        self.remember_position()
        self.enabled = False
        self.after_cancel(self._topmost_job)
        self.destroy()
