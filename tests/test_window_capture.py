"""Native capture checks use only test-owned windows; no private screen pixels."""
import os
from unittest.mock import patch

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows capture")


def test_window_capture_tracks_client_while_moved_covered_and_resized():
    import tkinter as tk
    import time
    from lib.logic.window_capture import list_windows
    from lib.logic.monitor_utils import ScreenCapture
    root = tk.Tk()
    root.title("Blackjack capture fixture")
    root.geometry("320x240+60+60")
    root.configure(bg="#20a060")
    tk.Frame(root, bg="#e04020", borderwidth=0).place(relx=.25, rely=.25, relwidth=.125, relheight=.125)
    root.update()
    cover = None
    try:
        with patch("lib.logic.window_capture.os.getpid", return_value=-1):
            source = next(w for w in list_windows() if w.title == root.title())
        capture = ScreenCapture()
        capture.set_monitor(source)
        first = capture.grab_bgr()
        assert first.shape == (240, 320, 3)
        assert np.allclose(first[100, 100], [96, 160, 32], atol=2)
        assert np.allclose(first[70, 90], [32, 64, 224], atol=2)
        root.geometry("320x240+250+200")
        root.update()
        assert np.array_equal(first, capture.grab_bgr())
        cover = tk.Toplevel(root)
        cover.geometry("400x320+220+170")
        cover.configure(bg="#ff0000")
        cover.lift()
        root.update()
        assert np.array_equal(first, capture.grab_bgr()), "Covering app leaked into capture"
        root.geometry("640x480+250+200")
        root.update()
        time.sleep(.2)
        root.update()
        resized = capture.grab_bgr()
        assert resized.shape == first.shape
        # Native edge/border antialiasing changes on resize; landmarks must
        # remain at their calibrated normalized client coordinates.
        assert np.allclose(resized[70, 90], [32, 64, 224], atol=2)
        assert np.allclose(resized[100, 100], [96, 160, 32], atol=2)
        root.iconify()
        root.update()
        with pytest.raises(RuntimeError, match="minimized"):
            capture.grab_bgr()
        root.deiconify()
        root.update()
        cover.destroy()
        cover = None
        root.destroy()
        with pytest.raises(RuntimeError, match="closed"):
            capture.grab_bgr()
    finally:
        try:
            if cover is not None:
                cover.destroy()
            root.destroy()
        except tk.TclError:
            pass


def test_failed_or_black_window_never_falls_back_to_monitor():
    from lib.logic.window_capture import WindowSource
    from lib.logic.monitor_utils import ScreenCapture
    capture = ScreenCapture()
    capture.set_monitor(WindowSource(0, "closed", 0, 320, 240))
    with patch.object(capture, "_sct", side_effect=AssertionError("Desktop fallback forbidden")):
        with pytest.raises(RuntimeError, match="closed"):
            capture.grab_bgr()
