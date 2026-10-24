"""Record redacted table ROIs, with an explicit duration and disk budget.

.venv312\\Scripts\\python.exe -m tools.record_table --config data/table.json --session session_a
"""
import argparse
import ctypes
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import mss
import numpy as np

from tools.common import ROOT, capture_roi, change_score, digest, load_config, table_anchors_match, write_json


def record(args):
    cfg = load_config(args.config)
    if Path(args.session).name!=args.session or args.session in (".",".."):
        raise ValueError("Session must be a plain new directory name")
    if cfg.get("table_kind")!="physical_shoe":
        raise ValueError("Phase 0 must verify a physical shoe before live recording")
    if not cfg.get("anchors"):
        raise ValueError("Add reviewed static table anchors before recording; prevents saving another app after window occlusion")
    if not 0 < args.fps <= 2 or not 0 < args.seconds <= 3600:
        raise ValueError("Capture budget: at most 2 fps and 3600 seconds per session")
    if not 0 < args.max_mb <= 500:
        raise ValueError("Per-session disk budget must be <=500 MB")
    out = ROOT / "data" / args.session
    if out.exists():
        raise ValueError("Use a new session name; recordings are never overwritten")
    out.mkdir(parents=True)
    write_json(out / "session.json", {"config": cfg, "role": args.role,
               "created": datetime.now(timezone.utc).isoformat(), "fps": args.fps})
    start = time.monotonic()
    next_capture, saved, attempts, used, previous = start, 0, 0, 0, None
    reason = "duration"
    invisible=0
    keep_awake = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    if keep_awake:
        keep_awake.SetThreadExecutionState(0x80000001)
    try:
        with mss.MSS() as sct, (out / "frames.jsonl").open("w", encoding="utf-8") as log:
            while time.monotonic()-start < args.seconds:
                time.sleep(max(0, next_capture-time.monotonic()))
                image = capture_roi(sct, cfg)
                attempts += 1
                next_capture = max(next_capture+1/args.fps, time.monotonic())
                if float(np.mean(np.max(image,axis=2)>30))<.04 or not table_anchors_match(image,cfg):
                    invisible+=1
                    if invisible==10:
                        write_json(out/"progress.json",{"status":"table_black_or_occluded","saved":saved,
                            "message":"Reactivate the table manually. No mouse or reload is simulated."})
                        print("TABLE_NOT_VISIBLE: reactivate the table manually",flush=True)
                    continue
                invisible=0
                thumb, score = change_score(previous, image)
                if score < args.change_threshold:
                    continue
                ok, encoded = cv2.imencode(".png", image)
                if not ok:
                    raise RuntimeError("PNG encoding failed")
                if used+encoded.nbytes > args.max_mb*1024**2:
                    reason = "disk_budget"
                    break
                previous = thumb
                name = f"frame_{saved:06d}.png"
                (out/name).write_bytes(encoded.tobytes())
                row = {"image": name, "frame_id": f"{args.session}:{saved}",
                       "session": args.session, "role": args.role,
                       "timestamp": datetime.now(timezone.utc).isoformat(),
                       "elapsed_s": round(time.monotonic()-start, 3),
                       "capture_index": attempts, "sha256": digest(image),
                       "resolution": cfg["host_resolution"],
                       "roi_size": list(image.shape[1::-1]), "redacted": True,
                       "change_score": score if previous is not None and score != float("inf") else None}
                log.write(json.dumps(row)+"\n")
                log.flush()
                used += encoded.nbytes
                saved += 1
                if saved % 20 == 0:
                    write_json(out/"progress.json", {"saved": saved, "elapsed_s": time.monotonic()-start,
                                                    "bytes": used, "status": "recording"})
    except KeyboardInterrupt:
        reason = "interrupted"
    except Exception:
        reason = "error"
        raise
    finally:
        if keep_awake:
            keep_awake.SetThreadExecutionState(0x80000000)
        summary = {"saved": saved, "attempts": attempts, "bytes": used,
                   "elapsed_s": round(time.monotonic()-start, 3), "stop_reason": reason,
                   "verified_rounds": 0, "human_reviewed_frames": 0}
        write_json(out/"progress.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--role", choices=["train", "eval"], default="train")
    parser.add_argument("--seconds", type=float, default=1800)
    parser.add_argument("--fps", type=float, default=2)
    parser.add_argument("--max-mb", type=float, default=240)
    parser.add_argument("--change-threshold", type=float, default=1.0)
    record(parser.parse_args())
