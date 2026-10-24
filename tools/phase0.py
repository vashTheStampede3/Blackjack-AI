"""Report feasibility without saving full desktop screenshots or guessing the provider."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import cv2
import mss

from tools.common import ROOT, capture_roi, load_config, write_json


def main(args):
    out=ROOT/"reports"; out.mkdir(exist_ok=True)
    with mss.MSS() as sct:
        monitor=sct.monitors[1]
        evidence={"timestamp":datetime.now(timezone.utc).isoformat(),
            "host_resolution":[monitor["width"],monitor["height"]],
            "provider":"UNVERIFIED","table":"UNVERIFIED","decision":"PAUSE_LIVE_VALIDATION"}
        try:
            cfg=load_config(args.config)
            image=capture_roi(sct,cfg)
            cv2.imwrite(str(out/"table_redacted.png"),image)
            evidence.update({k:cfg.get(k) for k in ["provider","table","table_kind","index_height_px",
                "deck_count_verified","penetration_observed"]})
            evidence["roi_size"]=list(image.shape[1::-1])
            if cfg.get("table_kind") in ["physical_shoe"] and cfg.get("index_height_px",0)>=24 and cfg.get("provider") not in [None,"UNVERIFIED"]:
                evidence["decision"]="CONTINUE"
        except (ValueError,OSError) as error:
            evidence["blocking_reason"]=str(error)
    write_json(out/"phase0.json",evidence)
    text=["# Phase 0 — feasibility","",f"UTC: {evidence['timestamp']}",
        f"Host: {evidence['host_resolution'][0]}×{evidence['host_resolution'][1]}.",
        f"Decision: **{evidence['decision']}**.","",
        "Earlier screenshots showed ‘VIP Blackjack en Español 7’ and a physical-looking dealing shoe; these are historical observations, not a current provider/penetration verification.",
        "Provider, exact current table, guest resolution, seats, index height, shoe type and penetration still require a reviewed current ROI.",
        "An alliance between a casino and a supplier does not identify an individual table.",
        "An index smaller than 24–30 native pixels remains a resolution risk; digital enlargement does not recover lost information.",
        "No full-screen image was persisted. No casino interaction was performed.",
        "",evidence.get("blocking_reason","")]
    (out/"phase0.md").write_text("\n".join(text)+"\n",encoding="utf-8")
    print(evidence["decision"])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--config",default="data/table.json")
    main(p.parse_args())
