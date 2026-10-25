"""Recorder lifecycle with synthetic frames and a fake mss/clock, no desktop data."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from tools import record_table as recorder
from tools.common import capture_roi


def setup_recorder(tmp_path,monkeypatch,anchors=True):
    cfg={"table_kind":"physical_shoe","anchors":[{}],"host_resolution":[160,96]}
    monkeypatch.setattr(recorder,"ROOT",tmp_path)
    monkeypatch.setattr(recorder,"load_config",lambda path:cfg)
    ticks=[0.]
    monkeypatch.setattr(recorder,"time",SimpleNamespace(monotonic=lambda:ticks[0],sleep=lambda seconds:ticks.__setitem__(0,ticks[0]+seconds)))
    class FakeSct:
        def __enter__(self): return self
        def __exit__(self,*args): return False
    monkeypatch.setattr(recorder.mss,"MSS",FakeSct)
    original=np.full((96,160,3),100,np.uint8)
    changed=original.copy();changed[20:35,20:35]=230
    frames=iter([original,original,changed])
    monkeypatch.setattr(recorder,"capture_roi",lambda *a:next(frames))
    monkeypatch.setattr(recorder,"table_anchors_match",lambda *a:anchors)
    awake=[]
    monkeypatch.setattr(recorder.ctypes,"windll",SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=lambda value:awake.append(value))))
    return SimpleNamespace(config="unused",session="test_session",role="train",seconds=1.25,
                           fps=2,max_mb=1,change_threshold=1),awake


def test_recorder_changed_only_and_restores_awake_state(tmp_path,monkeypatch):
    args,awake=setup_recorder(tmp_path,monkeypatch)
    recorder.record(args)
    directory=tmp_path/"data/test_session"
    rows=[json.loads(line) for line in (directory/"frames.jsonl").read_text().splitlines()]
    assert len(rows)==2
    assert rows[0]["roi_size"]==[160,96]
    assert all(row["redacted"] for row in rows)
    assert awake==[0x80000001,0x80000000]
    assert json.loads((directory/"progress.json").read_text())["stop_reason"]=="duration"


def test_occluded_table_is_never_saved(tmp_path,monkeypatch):
    args,awake=setup_recorder(tmp_path,monkeypatch,anchors=False)
    recorder.record(args)
    assert not list((tmp_path/"data/test_session").glob("*.png"))
    assert awake[-1]==0x80000000


def test_session_path_cannot_escape_data_directory(tmp_path,monkeypatch):
    args,_=setup_recorder(tmp_path,monkeypatch)
    args.session="../escape"
    with pytest.raises(ValueError,match="plain new directory"):
        recorder.record(args)
    assert not (tmp_path/"escape").exists()


def test_capture_requests_only_roi_and_masks_before_return():
    requested=[]
    class FakeSct:
        monitors=[{}, {"left":100,"top":0,"width":320,"height":240}]
        def grab(self,rect):
            requested.append(rect)
            return np.full((rect["height"],rect["width"],4),255,np.uint8)
    cfg={"roi":[10,20,110,120],"host_resolution":[320,240],"masks":[[0,.5,1,1]]}
    image=capture_roi(FakeSct(),cfg)
    assert requested==[{"left":110,"top":20,"width":100,"height":100}]
    assert image.shape==(100,100,3)
    assert not image[50:].any() and image[:50].all()
