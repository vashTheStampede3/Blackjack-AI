"""Desktop bootstrap tests: no Tk imports, windows, application or capture."""
import ast
from contextlib import contextmanager
import io
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import launch_blackjack as launcher


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "project with spaces"
    for relative in ("main.py", ".venv312/Scripts/python.exe", ".venv312/Scripts/pythonw.exe"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic fixture", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(root / ".venv312/Scripts/python.exe"))
    monkeypatch.setattr(launcher, "show_error", Mock())
    monkeypatch.setattr(launcher.runpy, "run_path", Mock(return_value={}))

    @contextmanager
    def first_instance(project):
        assert project == root
        yield True

    monkeypatch.setattr(launcher, "single_instance", first_instance)
    return root


def test_project_root_comes_from_launcher_location_not_current_directory(tmp_path, monkeypatch):
    root = tmp_path / "project"
    monkeypatch.chdir(tmp_path)
    assert launcher.project_root(root / "scripts" / "launch_blackjack.py") == root
    assert launcher.project_root() == Path(launcher.__file__).resolve().parents[1]


def test_validated_entry_is_the_official_main(installation):
    assert launcher.validate_installation(installation) == installation / "main.py"


@pytest.mark.parametrize("missing", ["main.py", ".venv312/Scripts/python.exe", ".venv312/Scripts/pythonw.exe"])
def test_missing_required_files_are_reported_before_importing_app(installation, missing):
    (installation / missing).unlink()
    with pytest.raises(FileNotFoundError, match="Missing required startup file"):
        launcher.validate_installation(installation)
    launcher.runpy.run_path.assert_not_called()


def test_a_directory_cannot_substitute_for_main(installation):
    entry = installation / "main.py"
    entry.unlink()
    entry.mkdir()
    with pytest.raises(FileNotFoundError):
        launcher.validate_installation(installation)


def test_wrong_interpreter_is_rejected(installation, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(installation / "system-python.exe"))
    with pytest.raises(RuntimeError, match="virtual environment"):
        launcher.validate_installation(installation)


@pytest.mark.parametrize("python_name", ["python.exe", "pythonw.exe"])
def test_both_project_python_launch_modes_are_accepted(installation, monkeypatch, python_name):
    monkeypatch.setattr(sys, "executable", str(installation / ".venv312/Scripts" / python_name))
    assert launcher.validate_installation(installation) == installation / "main.py"


def test_entry_runs_once_with_root_cwd_and_import_path_and_restores_process_state(installation, monkeypatch):
    original_cwd, original_path = Path.cwd(), list(sys.path)
    called = []

    def official_entry(path, *, run_name):
        assert Path.cwd() == installation
        assert sys.path[0] == str(installation)
        assert path == str(installation / "main.py") and run_name == "__main__"
        called.append(path)

    monkeypatch.setattr(launcher.runpy, "run_path", official_entry)
    assert launcher.main(installation) == 0
    assert len(called) == 1
    assert Path.cwd() == original_cwd and sys.path == original_path
    assert not (installation / "logs/startup_errors.log").exists()


def test_unexpected_startup_error_is_logged_and_visible_in_diagnostic_console(installation, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(sys, "stderr", output)
    launcher.runpy.run_path.side_effect = RuntimeError("synthetic startup failure")
    cwd, imports = Path.cwd(), list(sys.path)
    assert launcher.main(installation) == 1
    log = installation / "logs/startup_errors.log"
    assert log.is_file()
    text = log.read_text(encoding="utf-8")
    assert "Traceback" in text and "RuntimeError: synthetic startup failure" in text
    assert str(installation) in text and sys.executable in text
    assert "synthetic startup failure" in output.getvalue() and str(log) in output.getvalue()
    assert Path.cwd() == cwd and sys.path == imports
    launcher.show_error.assert_not_called()


def test_pythonw_failure_logs_and_notifies_without_tkinter(installation, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    launcher.runpy.run_path.side_effect = ImportError("synthetic missing module")
    assert launcher.main(installation) == 1
    launcher.show_error.assert_called_once()
    message = launcher.show_error.call_args.args[0]
    assert "synthetic missing module" in message
    assert str(installation / "logs/startup_errors.log") in message


def test_missing_main_is_also_logged_and_does_not_execute_entry(installation):
    (installation / "main.py").unlink()
    assert launcher.main(installation) == 1
    assert "FileNotFoundError" in (installation / "logs/startup_errors.log").read_text(encoding="utf-8")
    launcher.runpy.run_path.assert_not_called()


def test_error_log_appends_instead_of_erasing_previous_errors(installation):
    for message in ("first error", "second error"):
        launcher.log_startup_error(installation, RuntimeError(message))
    content = (installation / "logs/startup_errors.log").read_text(encoding="utf-8")
    assert "first error" in content and "second error" in content


def test_unwritable_project_log_falls_back_to_local_appdata(installation, tmp_path, monkeypatch):
    local = tmp_path / "local appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    original_open = Path.open

    def open_file(path, *args, **kwargs):
        if path == installation / "logs/startup_errors.log":
            raise PermissionError("synthetic read-only project")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    destination = launcher.log_startup_error(installation, RuntimeError("fallback error"))
    assert destination == local / "Blackjack-AI/startup_errors.log"
    assert "fallback error" in destination.read_text(encoding="utf-8")


def test_log_write_failure_is_not_silently_ignored(installation, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    launcher.runpy.run_path.side_effect = RuntimeError("original failure")
    monkeypatch.setattr(launcher, "log_startup_error", Mock(side_effect=OSError("disk unavailable")))
    assert launcher.main(installation) == 1
    message = launcher.show_error.call_args.args[0]
    assert "original failure" in message and "disk unavailable" in message


def test_duplicate_instance_does_not_execute_app_or_create_error_log(installation, monkeypatch):
    @contextmanager
    def duplicate(root):
        yield False

    monkeypatch.setattr(launcher, "single_instance", duplicate)
    assert launcher.main(installation) == 0
    launcher.runpy.run_path.assert_not_called()
    assert not (installation / "logs/startup_errors.log").exists()


@pytest.mark.parametrize("code,expected", [(0, 0), (None, 0), (2, 1)])
def test_official_entry_exit_codes_are_handled(installation, code, expected):
    launcher.runpy.run_path.side_effect = SystemExit(code)
    assert launcher.main(installation) == expected
    assert (installation / "logs/startup_errors.log").exists() == bool(expected)


def test_mutex_name_is_stable_per_project_and_different_for_another_project(tmp_path):
    root = tmp_path / "one"
    assert launcher.mutex_name(root) == launcher.mutex_name(root / "scripts" / "..")
    assert launcher.mutex_name(root) != launcher.mutex_name(tmp_path / "two")
    assert launcher.mutex_name(root).startswith("Local\\BlackjackAI_")


@pytest.mark.parametrize("existing", [False, True])
def test_windows_mutex_holds_handle_until_context_exit_and_closes_it(monkeypatch, tmp_path, existing):
    kernel = SimpleNamespace(CreateMutexW=Mock(return_value=123), CloseHandle=Mock(return_value=1))
    monkeypatch.setattr(launcher, "WINDOWS", True)
    monkeypatch.setattr(launcher, "_windows_api", lambda: kernel)
    monkeypatch.setattr(launcher.ctypes, "set_last_error", Mock(), raising=False)
    monkeypatch.setattr(launcher.ctypes, "get_last_error", lambda: 183 if existing else 0, raising=False)
    with launcher.single_instance(tmp_path) as first:
        assert first is not existing
        kernel.CloseHandle.assert_not_called()
        kernel.CreateMutexW.assert_called_once_with(None, False, launcher.mutex_name(tmp_path))
    kernel.CloseHandle.assert_called_once_with(123)


def test_launcher_delegates_without_importing_gui_or_starting_detection(installation, monkeypatch):
    import builtins
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "lib" or name.startswith("lib.") or name.split(".")[0] in {"tkinter", "cv2", "ultralytics"}:
            raise AssertionError(f"Launcher must delegate: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert launcher.main(installation) == 0
    launcher.runpy.run_path.assert_called_once_with(str(installation / "main.py"), run_name="__main__")
    tree = ast.parse(Path(launcher.__file__).read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {"start", "warm_up", "set_monitor"} for node in ast.walk(tree))


def test_installer_uses_own_location_known_desktop_and_one_shortcut():
    script = Path(launcher.__file__).with_name("install_desktop_shortcut.ps1").read_text(encoding="utf-8")
    assert "$PSScriptRoot" in script and "SpecialFolder]::DesktopDirectory" in script
    assert "Blackjack AI.lnk" in script and ".venv312\\Scripts\\pythonw.exe" in script
    assert "$shortcut.WorkingDirectory = $projectRoot" in script
    assert "Set-ExecutionPolicy" not in script and "RunAs" not in script
    assert "Remove-Item" not in script and "C:\\Users\\" not in script
