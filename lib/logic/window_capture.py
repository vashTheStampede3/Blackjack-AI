"""Read-only Windows client capture, independent of desktop position/occlusion."""
import ctypes
from ctypes import wintypes as W
from dataclasses import dataclass, replace
import os

import cv2
import numpy as np
import psutil


def _api():
    if os.name != "nt":
        raise RuntimeError("Window capture requires Windows.")
    u = ctypes.WinDLL("user32", use_last_error=True)
    g = ctypes.WinDLL("gdi32", use_last_error=True)
    signatures = [
        (u, "IsWindow", [W.HWND], W.BOOL),
        (u, "IsWindowVisible", [W.HWND], W.BOOL),
        (u, "IsIconic", [W.HWND], W.BOOL),
        (u, "IsHungAppWindow", [W.HWND], W.BOOL),
        (u, "GetClientRect", [W.HWND, ctypes.POINTER(W.RECT)], W.BOOL),
        (u, "GetWindowTextLengthW", [W.HWND], ctypes.c_int),
        (u, "GetWindowTextW", [W.HWND, W.LPWSTR, ctypes.c_int], ctypes.c_int),
        (u, "GetClassNameW", [W.HWND, W.LPWSTR, ctypes.c_int], ctypes.c_int),
        (u, "GetDC", [W.HWND], W.HDC),
        (u, "ReleaseDC", [W.HWND, W.HDC], ctypes.c_int),
        (u, "PrintWindow", [W.HWND, W.HDC, W.UINT], W.BOOL),
        (g, "CreateCompatibleDC", [W.HDC], W.HDC),
        (g, "CreateCompatibleBitmap", [W.HDC, ctypes.c_int, ctypes.c_int], W.HBITMAP),
        (g, "SelectObject", [W.HDC, W.HANDLE], W.HANDLE),
        (g, "DeleteObject", [W.HANDLE], W.BOOL),
        (g, "DeleteDC", [W.HDC], W.BOOL),
        (g, "GetDIBits", [W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT], ctypes.c_int),
        (u, "GetWindowThreadProcessId", [W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
    ]
    for dll, name, args, result in signatures:
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, result
    return u, g


def _dimensions(u, hwnd):
    rect = W.RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("The selected window is closed. Select it again.")
    return rect.right - rect.left, rect.bottom - rect.top


@dataclass(frozen=True)
class WindowSource:
    hwnd: int
    title: str
    pid: int
    width: int
    height: int
    x: int = 0
    y: int = 0
    application: str = ""
    minimized: bool = False
    class_name: str = ""

    @property
    def display_name(self):
        app = os.path.splitext(self.application)[0] or "Window"
        title = " ".join((self.title or "").split())
        if len(title) > 48:
            title = title[:45].rstrip() + "…"
        state = " · minimized" if self.minimized else ""
        return f"{app} · {title or 'Untitled'} — {self.width}×{self.height}{state}"


    def ready_source(self):
        """Resolve geometry only after restoring; enumeration must not drop it."""
        u, _ = _api()
        if not u.IsWindow(self.hwnd):
            raise RuntimeError("Window closed. Refresh windows.")
        if u.IsIconic(self.hwnd):
            raise RuntimeError("Window is minimized. Restore it, then select it again.")
        width, height = _dimensions(u, self.hwnd)
        if width < 2 or height < 2:
            raise RuntimeError("Window has no client area yet. Restore it and select again.")
        if self.minimized or self.width < 2 or self.height < 2:
            return replace(self, width=width, height=height, minimized=False)
        return self

    def grab_bgr(self):
        u, g = _api()
        pid = W.DWORD()
        u.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        if not u.IsWindow(self.hwnd) or pid.value != self.pid:
            raise RuntimeError("Selected window closed. Refresh windows and select it again.")
        if u.IsIconic(self.hwnd):
            raise RuntimeError("Selected window is minimized. Restore it; it can stay behind other windows.")
        if u.IsHungAppWindow(self.hwnd):
            raise RuntimeError("Selected window is not responding.")
        width, height = _dimensions(u, self.hwnd)
        if width < 2 or height < 2:
            raise RuntimeError("Selected window has no drawable client area.")
        dc = u.GetDC(self.hwnd)
        mem = bitmap = previous = None
        try:
            mem = g.CreateCompatibleDC(dc)
            bitmap = g.CreateCompatibleBitmap(dc, width, height)
            if not dc or not mem or not bitmap:
                raise RuntimeError("Cannot allocate window capture surface.")
            previous = g.SelectObject(mem, bitmap)
            # PW_CLIENTONLY | PW_RENDERFULLCONTENT. Never fall back to desktop
            # pixels: another app covering the target must never enter OCR.
            if not u.PrintWindow(self.hwnd, mem, 3):
                raise RuntimeError("This app does not support background capture.")
            g.SelectObject(mem, previous)
            previous = None
            header = (ctypes.c_uint32 * 10)()
            header[0], header[1], header[2] = 40, width, (-height) & 0xffffffff
            header[3] = 1 | (32 << 16)
            frame = np.empty((height, width, 4), dtype=np.uint8)
            if g.GetDIBits(mem, bitmap, 0, height, frame.ctypes.data, header, 0) != height:
                raise RuntimeError("Cannot read window capture pixels.")
            result = frame[:, :, :3].copy()
            if int(result.max()) < 3:
                raise RuntimeError("Window returned a black image. Keep its video active or use Monitor mode.")
            if (width, height) != (self.width, self.height):
                result = cv2.resize(result, (self.width, self.height))
            return result
        finally:
            if previous and mem:
                g.SelectObject(mem, previous)
            if bitmap:
                g.DeleteObject(bitmap)
            if mem:
                g.DeleteDC(mem)
            if dc:
                u.ReleaseDC(self.hwnd, dc)


def window_display_labels(windows):
    """Human labels for a window list; duplicate captions get a PID tie-breaker."""
    base = [window.display_name for window in windows]
    counts = {label: base.count(label) for label in set(base)}
    labels = [f"{label} · PID {window.pid}" if counts[label] > 1 else label
              for window, label in zip(windows, base)]
    duplicates = {}
    for i, label in enumerate(labels):
        duplicates.setdefault(label, []).append(i)
    for same_label in duplicates.values():
        if len(same_label) > 1:
            for number, index in enumerate(same_label, 1):
                labels[index] = f"{labels[index]} · Window {number}"
    return labels


def source_descriptor(source, kind):
    """A non-secret, non-process-identity hint for matching saved calibration."""
    if kind == "Window":
        app = os.path.splitext(source.application)[0].casefold()
        cls = (source.class_name or "").casefold()
        return {"kind": "window", "source_id": f"{app}|{cls}",
                "width": int(source.width), "height": int(source.height)}
    return {"kind": "monitor", "source_id": str(getattr(source, "name", "") or "monitor"),
            "width": int(source.width), "height": int(source.height)}


def source_review_required(reference, descriptor, *, ambiguous=False, has_calibration=False):
    """Whether saved geometry must be explicitly reviewed for this source."""
    if not reference:
        return bool(has_calibration)
    return reference != descriptor or bool(ambiguous)


def list_windows():
    """Visible top-level windows, including minimized ones; no activation/input."""
    u, _ = _api()
    windows = []
    callback_type = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    @callback_type
    def visit(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        length = u.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        text = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, text, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, len(cls))
        pid = W.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return True
        try:
            width, height = _dimensions(u, hwnd)
        except RuntimeError:
            return True  # window closed during enumeration
        minimized = bool(u.IsIconic(hwnd))
        if minimized or (width > 1 and height > 1):
            try:
                application = psutil.Process(pid.value).name()
            except (psutil.Error, OSError):
                application = ""
            # Shell/input infrastructure is not an application capture target.
            if application.casefold() in {"textinputhost.exe", "shellexperiencehost.exe"} or text.value == "Program Manager":
                return True
            windows.append(WindowSource(int(hwnd), text.value, pid.value, width, height,
                                        application=application, minimized=minimized,
                                        class_name=cls.value))
        return True
    u.EnumWindows(visit, 0)
    return sorted(windows, key=lambda item: (item.application.casefold(), item.title.casefold()))
