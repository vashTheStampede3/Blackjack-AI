"""Marshal fallback overlay hiding to Tk without worker-thread widget calls."""
from contextlib import contextmanager
import queue
import sys
import threading


def apply_capture_guard(hwnd):
    """Ask Windows to exclude an overlay HWND from supported screen captures."""
    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes
    try:
        fn = ctypes.windll.user32.SetWindowDisplayAffinity
        fn.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        fn.restype = ctypes.c_bool
        if not fn(hwnd, 0x11):  # WDA_EXCLUDEFROMCAPTURE
            return False
        affinity = ctypes.c_uint()
        get_affinity = ctypes.windll.user32.GetWindowDisplayAffinity
        get_affinity.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        get_affinity.restype = ctypes.c_bool
        return bool(get_affinity(hwnd, ctypes.byref(affinity)) and affinity.value == 0x11)
    except (AttributeError, OSError):
        return False


class CaptureGuard:
    def __init__(self, root, overlay_fn, source_fn=None):
        self.root, self.overlay_fn = root, overlay_fn
        self.source_fn = source_fn
        self.ui_thread = threading.get_ident()
        self.requests = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False
        self.force_hide = False
        self._timer = self.root.after(15, self._drain)

    def _drain(self):
        while True:
            try:
                fn, event, result, cancelled = self.requests.get_nowait()
            except queue.Empty:
                break
            try:
                if not cancelled.is_set():
                    result.append(fn())
            except Exception as exc:
                result.append(exc)
            finally:
                event.set()
        if not self.closed:
            self._timer = self.root.after(15, self._drain)

    def _call(self, fn):
        if self.closed:
            raise RuntimeError("Application is closing.")
        if threading.get_ident() == self.ui_thread:
            return fn()
        event, cancelled, result = threading.Event(), threading.Event(), []
        self.requests.put((fn, event, result, cancelled))
        if not event.wait(2):
            cancelled.set()
            raise RuntimeError("Capture paused: interface is busy.")
        if result and isinstance(result[0], Exception):
            raise result[0]
        return result[0] if result else None

    @contextmanager
    def capture(self):
        # UI cannot wait on a worker waiting on the UI: fail safely instead.
        acquired = self.lock.acquire(blocking=threading.get_ident() != self.ui_thread)
        if not acquired:
            raise RuntimeError("Capture in progress; try again.")
        hidden = False
        try:
            def hide():
                overlay = self.overlay_fn()
                if overlay and (getattr(overlay, "is_dragging", False)
                                or getattr(overlay, "_is_dragging", False)
                                or getattr(overlay, "_pointer_down", False)):
                    # During user drag, skip temporary window hide/restore so as not to interrupt Windows drag loop.
                    return False
                if overlay and not self.force_hide and self._external_window_source():
                    # PrintWindow renders only the external target HWND's client
                    # area. A separate top-level overlay cannot be in that image.
                    return False
                if (overlay and not self.force_hide and self._monitor_source()
                        and getattr(overlay, "affinity_ok", False)):
                    # Verified on this Windows host: MSS desktop capture omits
                    # a Tk fixture carrying WDA_EXCLUDEFROMCAPTURE. Keep the
                    # native exclusion active and avoid per-frame withdraw/show.
                    return False
                if overlay:
                    overlay.set_capture_hidden(True)
                    return overlay
                return False
            hidden = self._call(hide)
            yield
        finally:
            try:
                if hidden and not self.closed:
                    def restore():
                        hidden.set_capture_hidden(False)
                    self._call(restore)
            finally:
                self.lock.release()

    def _external_window_source(self):
        if self.source_fn is None:
            return False
        try:
            source = self.source_fn()
            hwnd = getattr(source, "hwnd", None)
            if not hwnd:
                return False
            import ctypes
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(
                ctypes.c_void_p(int(hwnd)), ctypes.byref(pid))
            return bool(pid.value and pid.value != __import__("os").getpid())
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def _monitor_source(self):
        if self.source_fn is None:
            return False
        try:
            source = self.source_fn()
            return source is not None and not getattr(source, "hwnd", None)
        except Exception:
            return False

    def close(self):
        self.closed = True
        self.root.after_cancel(self._timer)
        while not self.requests.empty():
            try:
                _, event, result, cancelled = self.requests.get_nowait()
                cancelled.set()
                result.append(RuntimeError("Application is closing."))
                event.set()
            except queue.Empty:
                break
