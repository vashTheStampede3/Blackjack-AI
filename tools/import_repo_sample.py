"""30 redacted diagnostic frames from the repository's existing 10-second clip.

This is historical diagnostic footage, not either required live session.
"""
import hashlib
import json
from pathlib import Path

import cv2
from PIL import Image
from winocr import recognize_pil

from tools.common import ROOT,digest,redact,write_json
from lib.logic.vision_labels import canonical_rank


def main():
    source=ROOT/"archive/api/sample video.mp4"
    out=ROOT/"data/repo_video_diagnostic"
    out.mkdir(exist_ok=False)
    cfg={"roi":[420,780,2140,1400],"host_resolution":[2560,1440],
        "masks":[[0,.9,1,1],[0,.6,.18,1],[.82,.6,1,1]],
        "geometry_verified":True,"privacy_reviewed":True,
        "table":"historical repo sample","provider":"UNVERIFIED","table_kind":"physical_shoe"}
    write_json(out/"session.json",{"role":"diagnostic","config":cfg,
        "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_duration_s":10.0,"warning":"Not current Betano footage; no holdout ground truth",
        "extra_redaction":"Small non-rank OCR tokens masked locally; no token text stored"})
    capture=cv2.VideoCapture(str(source))
    count=int(capture.get(cv2.CAP_PROP_FRAME_COUNT)); fps=capture.get(cv2.CAP_PROP_FPS)
    with (out/"frames.jsonl").open("w",encoding="utf-8") as log:
        for i in range(30):
            index=round(i*(count-1)/29)
            capture.set(cv2.CAP_PROP_POS_FRAMES,index)
            ok,frame=capture.read()
            if not ok:
                raise RuntimeError("Source frame unavailable")
            l,t,r,b=cfg["roi"]
            safe=redact(frame[t:b,l:r],cfg)
            # Preserve native >=24-pixel card indices; obscure small HUD text.
            pil=Image.fromarray(cv2.cvtColor(safe,cv2.COLOR_BGR2RGB))
            result=recognize_pil(pil,lang="es-MX").get()
            for line in result.lines:
                for word in line.words:
                    rect=word.bounding_rect
                    if rect.height<24 and canonical_rank(word.text)=="unknown":
                        x,y=int(rect.x),int(rect.y)
                        safe[max(0,y-2):int(rect.y+rect.height)+3,max(0,x-2):int(rect.x+rect.width)+3]=0
            name=f"frame_{i:06d}.png"
            cv2.imwrite(str(out/name),safe)
            log.write(json.dumps({"image":name,"frame_id":f"repo_video:{index}","session":out.name,
                "role":"diagnostic","timestamp":index/fps,"elapsed_s":index/fps,"redacted":True,
                "roi_size":list(safe.shape[1::-1]),"resolution":[2560,1440],"sha256":digest(safe)})+"\n")
    capture.release()
    print("Imported 30 historical diagnostic frames; reviewed labels: 0; live sessions: 0")


if __name__=="__main__":
    main()
