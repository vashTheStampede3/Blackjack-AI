"""Read-only Windows client capture, independent of desktop position/occlusion."""
import ctypes
from ctypes import wintypes as W
from dataclasses import dataclass
import os

import cv2
import numpy as np


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
        pid = W.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return True
        width, height = _dimensions(u, hwnd)
        if width > 1 and height > 1:
            windows.append(WindowSource(int(hwnd), text.value, pid.value, width, height))
        return True
    u.EnumWindows(visit, 0)
    return sorted(windows, key=lambda item: item.title.casefold())
