"""Local CPU QDQ calibration, using a named non-holdout diagnostic session.

This optimizes a candidate only. Accuracy must be re-evaluated before promotion.
"""
import argparse
import gc
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import psutil
from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_static

from tools.common import ROOT,manifest,read_json,write_json
from lib.logic.models import OnnxModel,letterbox


def main(args):
    source=ROOT/args.weights; output=ROOT/args.output; rows_path=ROOT/args.manifest
    if output.exists():
        raise ValueError("Candidate output exists; do not overwrite")
    session=read_json(rows_path.parent/"session.json")
    if session["role"]=="eval":
        raise ValueError("Held-out evaluation images cannot calibrate quantization")
    rows=manifest(rows_path)
    original_session=ort.InferenceSession
    def limited_session(*a,**kw):
        opts=kw.get("sess_options") or ort.SessionOptions()
        opts.intra_op_num_threads=2; opts.inter_op_num_threads=1
        opts.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        kw["sess_options"]=opts
        kw["providers"]=["CPUExecutionProvider"]
        return original_session(*a,**kw)
    # The quantizer creates its own calibration session; cap that session too.
    ort.InferenceSession=limited_session
    model=OnnxModel(source)
    size,resize,input_name=model._size,model._resize_mode,model._input.name
    del model; gc.collect()
    class Reader(CalibrationDataReader):
        def __init__(self): self.index=0
        def get_next(self):
            if psutil.Process().memory_info().rss>1300*1024**2 or psutil.virtual_memory().available<512*1024**2:
                raise MemoryError("Quantization memory budget reached; move preparation off this PC")
            if self.index>=len(rows): return None
            row=rows[self.index]; self.index+=1
            if not row.get("redacted"): raise ValueError("Calibration frame is not redacted")
            frame=cv2.imread(str(rows_path.parent/row["image"]))
            if resize=="Stretch to": frame=cv2.resize(frame,(size,size))
            else: frame=letterbox(frame,size,color=(114,114,114) if resize=="letterbox" else (0,0,0))[0]
            return {input_name:np.ascontiguousarray(frame[:,:,::-1].transpose(2,0,1)[None],dtype=np.float32)/255}
    quantize_static(str(source),str(output),Reader(),quant_format=QuantFormat.QDQ,
        per_channel=True,activation_type=QuantType.QInt8,weight_type=QuantType.QInt8,
        op_types_to_quantize=["Conv"],calibration_providers=["CPUExecutionProvider"])
    info=read_json(Path(str(source)+".json"))
    info.update(quantized_from=source.name,quantization="QDQ S8S8 Conv per-channel",
                calibration_sessions=[rows_path.parent.name],bytes=output.stat().st_size,
                sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    write_json(Path(str(output)+".json"),info)
    print(json.dumps({"candidate":str(output.relative_to(ROOT)),"bytes":output.stat().st_size,
        "calibration_frames":len(rows),"accuracy_verified":False}))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--weights",required=True)
    p.add_argument("--output",required=True); p.add_argument("--manifest",required=True)
    main(p.parse_args())
