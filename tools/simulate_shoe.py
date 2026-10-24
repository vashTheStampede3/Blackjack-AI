"""Controlled eight-deck simulation. Results are synthetic, never live accuracy."""
import argparse
import json
import random

from tools.common import ROOT, write_json
from tools.detectors import Detection
from lib.logic.vision_counter import TemporalCounter
from lib.logic.vision_labels import RANKS, delta


def simulate(seed, confirmations, consensus=.8):
    rng=random.Random(seed)
    ranks=list(RANKS)*32
    rng.shuffle(ranks)
    c=TemporalCounter(confirmations=confirmations,consensus=consensus)
    expected=0; round_correct=0; rounds=0; duplicate_events=0
    for start in range(0,416,16):
        hand=ranks[start:start+16]; rounds+=1
        for frame in range(10):
            detections=[]
            for i,rank in enumerate(hand):
                if rng.random()<.1: # 10% independent misses
                    continue
                emitted=rng.choice(RANKS) if rng.random()<.03 else rank
                x=(i%8)*40+rng.uniform(-.8,.8); y=(i//8)*55+rng.uniform(-.8,.8)
                p=Detection(emitted,[x,y,x+20,y+30],.95,emitted)
                detections.append(p)
                if rng.random()<.15:
                    detections.append(p)
            c.observe(rounds,f"{rounds}:{frame}",detections)
            c.observe(rounds,f"{rounds}:{frame}",detections)
        expected+=sum(delta(r) for r in hand)
        round_correct+=c.running==expected
        duplicate_events+=max(0,len([e for e in c.events if e.get('round_id')==rounds])-len(hand))
    final=c.snapshot()
    c.reset_shoe("manual")
    return {"seed":seed,"rounds":rounds,"round_count_correct":round_correct,
            "round_count_accuracy":round_correct/rounds,"running_drift":final["running"]-expected,
            "cards_seen":final["cards_seen"],"missing_or_excess_cards":final["cards_seen"]-416,
            "double_count_events":duplicate_events,"manual_reset_ok":c.cards_seen==c.running==0}


def main(args):
    results={str(n):[simulate(seed,n,args.consensus) for seed in range(args.seeds)] for n in [1,3]}
    summary={}
    for n,rows in results.items():
        summary[n]={"rounds":sum(r["rounds"] for r in rows),
            "round_accuracy":sum(r["round_count_correct"] for r in rows)/sum(r["rounds"] for r in rows),
            "mean_absolute_running_drift":sum(abs(r["running_drift"]) for r in rows)/len(rows),
            "double_count_events":sum(r["double_count_events"] for r in rows),
            "manual_reset_all":all(r["manual_reset_ok"] for r in rows)}
    payload={"scope":"SYNTHETIC ONLY; no live validation", "parameters":{
        "decks":8,"frames_per_round":10,"consensus":args.consensus,"miss_probability":.1,
        "rank_confusion_probability":.03,"duplicate_probability":.15,"position_jitter_px":.8},
        "summary":summary,"runs":results}
    write_json(ROOT/"reports"/"count_simulation.json",payload)
    text=["# Synthetic counting experiment","","Not a Betano accuracy measurement.","",
          "Single hypothesis: change temporal confirmations from 1 to 3; same generated inputs and other settings.","",
          "| Confirmations | Rounds | End-round running accuracy | Mean absolute drift | Duplicates |",
          "|---|---:|---:|---:|---:|"]
    for n,s in summary.items():
        text.append(f"| {n} | {s['rounds']} | {s['round_accuracy']:.4%} | {s['mean_absolute_running_drift']:.3f} | {s['double_count_events']} |")
    (ROOT/"reports"/"count_simulation.md").write_text("\n".join(text)+"\n",encoding="utf-8")
    print(json.dumps(summary))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--seeds",type=int,default=30); p.add_argument("--consensus",type=float,default=.8)
    main(p.parse_args())
