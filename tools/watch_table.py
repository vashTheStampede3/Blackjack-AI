"""Find a visible felt/card candidate using mss only; never activate a window.

This emits a heavily redacted preview for agent review, not an approved ROI.
It does not record a session or infer casino provider/physical shoe type.
"""
import argparse
import time

import cv2
import mss
import numpy as np

from tools.common import ROOT,redact,write_json
from lib.logic.models import OnnxModel
from lib.logic.vision_labels import canonical_rank


def main(args):
    model=OnnxModel(ROOT/"models/roboflow/dey022_1.onnx")
    start=time.monotonic(); found=False; checks=0
    with mss.MSS() as sct:
        monitor=sct.monitors[1]
        while time.monotonic()-start<args.seconds:
            raw=np.asarray(sct.grab(monitor))[:,:,:3]
            hsv=cv2.cvtColor(raw,cv2.COLOR_BGR2HSV)
            colored=cv2.inRange(hsv,np.array([12,65,65]),np.array([95,255,255]))
            colored=cv2.morphologyEx(colored,cv2.MORPH_CLOSE,np.ones((15,15),np.uint8))
            contours,_=cv2.findContours(colored,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            contours=sorted(contours,key=cv2.contourArea,reverse=True)
            checks+=1
            for contour in contours[:3]:
                if cv2.contourArea(contour)<40000:
                    continue
                x,y,w,h=cv2.boundingRect(contour)
                if y<raw.shape[0]*.25 or not (1<w/max(h,1)<6):
                    continue
                l=max(0,x-15); t=max(0,y-40); r=min(raw.shape[1],x+w+15); b=min(raw.shape[0],y+h+10)
                cfg={"table":"UNVERIFIED","provider":"UNVERIFIED","table_kind":"unknown",
                    "monitor":1,"host_resolution":[monitor["width"],monitor["height"]],"roi":[l,t,r,b],
                    "masks":[[0,.72,1,1],[0,.5,.18,1],[.82,.5,1,1]],
                    "geometry_verified":False,"privacy_reviewed":False,"index_height_px":None}
                safe=redact(raw[t:b,l:r],cfg)
                preds=[p for p in model.predict(safe,70,45) if canonical_rank(p["class"])!="unknown"]
                if len(preds)<3:
                    continue
                write_json(ROOT/"reports"/"pending_roi.json",cfg)
                cv2.imwrite(str(ROOT/"reports"/"pending_table.png"),safe)
                found=True; break
            write_json(ROOT/"reports"/"visibility_watch.json",{"candidate_found":found,"checks":checks,
                "elapsed_s":round(time.monotonic()-start,1),"status":"candidate_needs_review" if found else "waiting_for_visible_table"})
            if found:
                break
            time.sleep(2)
    print("candidate_needs_review" if found else "No readable table candidate appeared; no session was recorded")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--seconds",type=int,default=300)
    main(p.parse_args())
