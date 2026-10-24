"""Marshal fallback overlay hiding to Tk without worker-thread widget calls."""
from contextlib import contextmanager
import queue
import threading


class CaptureGuard:
    def __init__(self, root, overlay_fn):
        self.root, self.overlay_fn = root, overlay_fn
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
                if overlay and (self.force_hide or not overlay.affinity_ok):
                    overlay.set_capture_hidden(True)
                    return True
                return False
            hidden = self._call(hide)
            yield
        finally:
            try:
                if hidden and not self.closed:
                    def restore():
                        overlay = self.overlay_fn()
                        if overlay:
                            overlay.set_capture_hidden(False)
                    self._call(restore)
            finally:
                self.lock.release()

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
