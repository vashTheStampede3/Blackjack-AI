"""Blackjack AI — entry point.

Run with:  python main.py
"""

from lib.common import constants, settings
import sys
from lib.logic.log_manager import LogManager, install_redirectors, install_exception_hooks


def main():
    # Per-monitor DPI awareness BEFORE any window exists, so screen-capture
    # coordinates, calibrated regions, and the overlay HUD line up on
    # scaled displays.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    # Logging first, so every later print is captured (stderr included).
    log_manager = LogManager()
    log_manager.start_session(constants.LOGS_DIR)
    install_redirectors(log_manager)
    install_exception_hooks(log_manager)
    log_manager.add_log(f"Archivo de esta sesión: {log_manager.session_path}",
                        event="SESSION_START")

    try:
        print(f"Starting {constants.TITLE}")
        if settings.load_and_apply():
            print("Loaded table profile from output/settings.json.")

        # Import after logging is live so startup failures are recorded too.
        from lib.interfaces.modern_gui import ModernBlackjackGUI

        gui = ModernBlackjackGUI(log_manager)
        print("UI ready.")
        gui.mainloop()
    except BaseException:
        log_manager.exception("Aplicación interrumpida")
        raise
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        log_manager.add_log("Aplicación cerrada.", event="SESSION_END")
        log_manager.close_session()


if __name__ == "__main__":
    main()
