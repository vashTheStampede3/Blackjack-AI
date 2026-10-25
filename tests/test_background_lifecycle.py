"""Worker lifecycle checks with a blocked fake cycle; no app or capture used."""
import threading
import time
from types import SimpleNamespace

from lib.logic.background import DetectionController


def test_stop_then_start_waits_for_old_cycle_before_reusing_engine():
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    calls = 0

    def run_cycle():
        nonlocal active, maximum_active, calls
        with lock:
            active += 1
            calls += 1
            number = calls
            maximum_active = max(maximum_active, active)
        try:
            if number == 1:
                first_entered.set()
                assert release_first.wait(2)
            else:
                second_entered.set()
            return "waiting"
        finally:
            with lock:
                active -= 1

    engine = SimpleNamespace(
        provider=SimpleNamespace(backend_name="fake"),
        capture=SimpleNamespace(close_local=lambda: None),
        current_phase=None,
        warm_up=lambda: None,
        run_cycle=run_cycle,
        health_check_and_refresh=lambda: True,
    )
    controller = DetectionController(log=lambda *args, **kwargs: None)
    controller.engine = engine

    assert controller.start()
    assert first_entered.wait(2)
    controller.stop()
    assert controller.start()  # queues restart instead of starting a 2nd cycle
    release_first.set()
    assert second_entered.wait(2)
    controller.stop()
    deadline = time.monotonic() + 2
    while controller._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.005)

    assert not controller._thread.is_alive()
    assert calls == 2
    assert maximum_active == 1
