"""Fail-closed table-only boundary for any hosted inference call."""
import json
import os
from pathlib import Path

import numpy as np

from ..common import constants


def private_table_frame(full_frame):
    path = Path(os.environ.get("BJ_TABLE_CONFIG", str(constants.PROJECT_ROOT/"data"/"table.json")))
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if not (cfg.get("geometry_verified") and cfg.get("privacy_reviewed")):
            raise ValueError("unreviewed table configuration")
        if list(full_frame.shape[1::-1]) != cfg["host_resolution"]:
            raise ValueError("expected full host frame; recalibrate changed geometry")
        l,t,r,b = map(int,cfg["roi"])
        if not (0<=l<r<=full_frame.shape[1] and 0<=t<b<=full_frame.shape[0]):
            raise ValueError("invalid table ROI")
        out = full_frame[t:b,l:r].copy()
        h,w = out.shape[:2]
        for ml,mt,mr,mb in cfg.get("masks",[]):
            if not (0<=ml<mr<=1 and 0<=mt<mb<=1):
                raise ValueError("invalid privacy mask")
            out[int(mt*h):int(np.ceil(mb*h)),int(ml*w):int(np.ceil(mr*w))] = 0
        return out,(l,t)
    except (OSError,ValueError,TypeError,KeyError):
        raise ValueError("Hosted inference paused: configure a reviewed table ROI and HUD masks in data/table.json") from None
