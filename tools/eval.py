"""Replay a fixed session; never report accuracy without reviewed ground truth.

python -m tools.eval --manifest data/session_b/frames.jsonl --labels data/session_b/labels.jsonl --weights models/player_cards.onnx
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import cv2
import numpy as np
import psutil

from tools.common import ROOT, manifest, read_json, redact, write_json
from tools.detectors import add_arguments, build, iou
from lib.logic.vision_labels import RANKS, bucket, canonical_rank, delta
from lib.logic.vision_counter import TemporalCounter


def match(truth, predictions, threshold=.3):
    candidates = sorted(((iou(t["box"],p.box),i,j) for i,t in enumerate(truth)
                         for j,p in enumerate(predictions)),reverse=True)
    paired, gt_used, pr_used = [],set(),set()
    for score,i,j in candidates:
        if score >= threshold and i not in gt_used and j not in pr_used:
            paired.append((i,j))
            gt_used.add(i)
            pr_used.add(j)
    return paired,set(range(len(truth)))-gt_used,set(range(len(predictions)))-pr_used


def score_frames(scored):
    confusion = defaultdict(Counter)
    total = correct = bucket_correct = predicted = 0
    for truth,preds in scored:
        pairs,misses,extras = match(truth,preds)
        total += len(truth)
        predicted += len(preds)
        for i,j in pairs:
            a,b = canonical_rank(truth[i]["rank"]),preds[j].rank
            confusion[a][b] += 1
            correct += a == b
            bucket_correct += bucket(a) == bucket(b) and b != "unknown"
        for i in misses:
            confusion[canonical_rank(truth[i]["rank"])]["MISS"] += 1
        for j in extras:
            confusion["FP"][preds[j].rank] += 1
    per_rank = {}
    for rank in RANKS:
        tp = confusion[rank][rank]
        support = sum(confusion[rank].values())
        emitted = sum(row[rank] for row in confusion.values())
        per_rank[rank] = {"precision":tp/emitted if emitted else None,
                          "recall":tp/support if support else None,"support":support}
    return {"ground_truth_observations":total,"predicted_observations":predicted,
            "rank_accuracy":correct/total if total else None,
            "bucket_accuracy":bucket_correct/total if total else None,
            "precision":correct/predicted if predicted else None,
            "per_rank":per_rank,"confusion":{k:dict(v) for k,v in confusion.items()}}


def evaluate(args):
    path = Path(args.manifest).resolve()
    rows = manifest(path)
    session_meta = read_json(path.parent/"session.json")
    role = session_meta["role"]
    cfg = session_meta.get("config",{})
    if not (cfg.get("privacy_reviewed") and cfg.get("geometry_verified")):
        raise ValueError("Recorded table ROI and privacy masks must be reviewed")
    if args.final and role != "eval":
        raise ValueError("Final acceptance must use a held-out evaluation session")
    labels = {r["image"]:r for r in manifest(args.labels)} if args.labels else {}
    detector = build(args)
    out = ROOT/"reports"/(args.name or datetime.now().strftime("eval_%Y%m%d_%H%M%S"))
    out.mkdir(parents=True,exist_ok=False)
    latency, scored, outputs, worst = [],[],[],[]
    process = psutil.Process()
    cpu_start = process.cpu_times()
    start = time.perf_counter()
    peak_rss = process.memory_info().rss
    counter = TemporalCounter(min_confidence=args.confidence)
    gt_rounds = defaultdict(dict)
    temporal = {}
    for row in rows[:args.limit or None]:
        if not row.get("redacted"):
            raise ValueError("Manifest lacks a verified redaction flag")
        source = (path.parent/row["image"]).resolve()
        if source.parent != path.parent:
            raise ValueError("Frame path escaped session directory")
        frame = cv2.imread(str(source))
        if frame is None or list(frame.shape[1::-1]) != row["roi_size"]:
            raise ValueError("Missing frame or inconsistent ROI dimensions")
        frame = redact(frame,cfg)
        t0 = time.perf_counter()
        preds = detector.predict(frame)
        latency.append((time.perf_counter()-t0)*1000)
        peak_rss = max(peak_rss,process.memory_info().rss)
        outputs.append({"image":row["image"],"frame_id":row["frame_id"],
                        "predictions":[p.asdict() for p in preds]})
        label = labels.get(row["image"])
        if not label or not label.get("reviewed"):
            continue
        truth = label["cards"]
        scored.append((truth,preds))
        pairs,misses,extras = match(truth,preds)
        errors = len(misses)+len(extras)+sum(canonical_rank(truth[i]["rank"])!=preds[j].rank for i,j in pairs)
        worst.append((errors,row["image"],truth,preds))
        round_id = label.get("round_id")
        if round_id is not None and all(c.get("card_id") for c in truth):
            counter.observe(round_id,row["frame_id"],preds)
            for c in truth:
                gt_rounds[round_id][c["card_id"]] = c
            temporal[round_id] = [{"rank":t.rank,"box":t.box,"id":t.identity} for t in counter.tracks]
    elapsed = time.perf_counter()-start
    cpu_end = process.cpu_times()
    cpu_seconds = cpu_end.user+cpu_end.system-cpu_start.user-cpu_start.system
    metrics = score_frames(scored)
    card_scored, correct_rounds, cumulative_truth, cumulative_pred = [],0,0,0
    from tools.detectors import Detection
    for rid,truth_map in gt_rounds.items():
        truth = list(truth_map.values())
        preds = [Detection(p["rank"],p["box"],1) for p in temporal[rid] if p["rank"]!="unknown"]
        card_scored.append((truth,preds))
        cumulative_truth += sum(delta(c["rank"]) for c in truth)
        cumulative_pred += sum(delta(p.rank) for p in preds)
        correct_rounds += cumulative_truth == cumulative_pred
    temporal_metrics = score_frames(card_scored)
    metrics.update({"session_role":role,"frames":len(outputs),"reviewed_frames":len(scored),
        "total_predicted_boxes":sum(len(row["predictions"]) for row in outputs),
        "frames_with_predictions":sum(bool(row["predictions"]) for row in outputs),
        "temporal":temporal_metrics,"annotated_rounds":len(gt_rounds),
        "running_count_round_accuracy":correct_rounds/len(gt_rounds) if gt_rounds else None,
        "latency_p50_ms":float(np.percentile(latency,50)) if latency else None,
        "latency_p95_ms":float(np.percentile(latency,95)) if latency else None,
        "process_cpu_percent_of_machine":100*cpu_seconds/max(elapsed,1e-9)/(psutil.cpu_count() or 1),
        "peak_rss_mb":peak_rss/1024**2,"elapsed_s":elapsed,
        "command_parameters":{k:v for k,v in vars(args).items()},
        "reshuffle_live_verified":False,"soak_30min_verified":False})
    required_support = all(temporal_metrics["per_rank"][r]["recall"] is not None and
        temporal_metrics["per_rank"][r]["recall"] >= .9 for r in RANKS)
    metrics["acceptance"] = {
        "300_unique_cards":temporal_metrics["ground_truth_observations"]>=300,
        "rank_accuracy_98":(temporal_metrics["rank_accuracy"] or 0)>=.98,
        "bucket_accuracy_99":(temporal_metrics["bucket_accuracy"] or 0)>=.99,
        "all_rank_recall_90":required_support,
        "40_rounds_count_95":len(gt_rounds)>=40 and correct_rounds/len(gt_rounds)>=.95,
        "p95_800ms":bool(latency) and metrics["latency_p95_ms"]<=800,
        "cpu_35":bool(latency) and metrics["process_cpu_percent_of_machine"]<=35,
        "ram_1536mb":bool(latency) and metrics["peak_rss_mb"]<=1536,
        "double_count_property":None,"reshuffle_3_shoes_or_manual":None,
        "soak_30min":False,"held_out_session":role=="eval",
        "live_cycle_p95_800ms":None,"live_app_cpu_35":None,"live_app_ram_1536mb":None,
        "shoe_origin_verified":bool(session_meta.get("shoe_origin_verified")),
        "model_training_provenance_verified":None,
        "human_cross_review_10_percent":bool(scored) and
            sum(bool(l.get("reviewed") and l.get("cross_reviewed")) for l in labels.values())>=len(scored)*.1}
    metrics["accepted"] = all(v is True for v in metrics["acceptance"].values())
    write_json(out/"metrics.json",metrics)
    (out/"predictions.jsonl").write_text("".join(json.dumps(p)+"\n" for p in outputs),encoding="utf-8")
    for n,(errors,name,truth,preds) in enumerate(sorted(worst,key=lambda v:v[0],reverse=True)[:20]):
        frame = cv2.imread(str(path.parent/name))
        boxes = [c["box"] for c in truth]+[p.box for p in preds]
        if not boxes:
            continue
        l=max(0,int(min(b[0] for b in boxes))-12); t=max(0,int(min(b[1] for b in boxes))-12)
        r=min(frame.shape[1],int(max(b[2] for b in boxes))+12); b=min(frame.shape[0],int(max(b[3] for b in boxes))+12)
        if r>l and b>t:
            cv2.imwrite(str(out/f"error_{n:02d}_{errors}.png"),frame[t:b,l:r])
    pretty = lambda v: "NOT MEASURED" if v is None else f"{100*v:.2f}%"
    report = ["# Offline evaluation", "", f"UTC: {datetime.now(timezone.utc).isoformat()}",
        f"Session role: {role}. Frames: {len(outputs)}; reviewed: {len(scored)}.",
        "", f"Frame rank accuracy: {pretty(metrics['rank_accuracy'])}",
        f"All frames: {metrics['total_predicted_boxes']} raw boxes, {metrics['frames_with_predictions']} nonempty frames (not a correctness metric).",
        f"Unique-card consensus accuracy: {pretty(temporal_metrics['rank_accuracy'])}",
        f"Hi-Lo bucket accuracy: {pretty(temporal_metrics['bucket_accuracy'])}",
        f"Annotated unique cards: {temporal_metrics['ground_truth_observations']}; rounds: {len(gt_rounds)}.",
        f"P50/P95 latency (ms): {metrics['latency_p50_ms']} / {metrics['latency_p95_ms']}",
        f"CPU (machine-normalized): {metrics['process_cpu_percent_of_machine']:.2f}%; peak RSS: {metrics['peak_rss_mb']:.1f} MB.",
        "", "| Gate | Result |", "|---|---|"]
    report += [f"| {k} | {v if v is not None else 'NOT MEASURED'} |" for k,v in metrics["acceptance"].items()]
    report += ["", "No holdout labels means no accuracy claim. No model is promoted automatically.",
        "CPU/RAM here cover this replay process, not the full live app. Full app soak remains required."]
    (out.with_suffix(".md")).write_text("\n".join(report)+"\n",encoding="utf-8")
    print(json.dumps({"report":str(out.with_suffix('.md')),"frames":len(outputs),
                      "reviewed":len(scored),"accepted":metrics["accepted"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--labels")
    parser.add_argument("--name")
    parser.add_argument("--limit",type=int,default=0)
    parser.add_argument("--final",action="store_true")
    add_arguments(parser)
    evaluate(parser.parse_args())
