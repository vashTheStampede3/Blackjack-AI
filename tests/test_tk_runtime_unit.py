from pathlib import Path
import _tkinter
from lib.interfaces.tk_runtime import configure_tk_runtime


def test_configures_only_bundled_existing_libraries_and_preserves_valid_override(tmp_path):
    for directory, name in ((f"tcl{_tkinter.TCL_VERSION}","init.tcl"),
                            (f"tk{_tkinter.TK_VERSION}","tk.tcl")):
        target=tmp_path/"tcl"/directory
        target.mkdir(parents=True)
        (target/name).write_text("fixture")
    env={"TCL_LIBRARY":"missing"}
    configured=configure_tk_runtime(tmp_path,env)
    assert set(configured)=={"TCL_LIBRARY","TK_LIBRARY"}
    assert all(Path(value).is_absolute() for value in configured.values())
    custom=tmp_path/"custom";custom.mkdir();(custom/"init.tcl").write_text("fixture")
    env["TCL_LIBRARY"]=str(custom)
    assert configure_tk_runtime(tmp_path,env)=={}
    assert env["TCL_LIBRARY"]==str(custom)


def test_missing_runtime_does_not_invent_paths(tmp_path):
    env={}
    assert configure_tk_runtime(tmp_path,env)=={} and env=={}
