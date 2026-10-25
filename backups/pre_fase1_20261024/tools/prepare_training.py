"""Build a private, rank-only Colab dataset with session/round split isolation."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import cv2

from tools.common import ROOT, manifest, read_json, write_json
from lib.logic.vision_labels import RANKS, canonical_rank


def prepare(args):
    train=Path(args.train).resolve(); evaluation=Path(args.eval).resolve()
    if train==evaluation:
        raise ValueError("Training and held-out evaluation must be different sessions")
    if read_json(train/"session.json")["role"]!="train" or read_json(evaluation/"session.json")["role"]!="eval":
        raise ValueError("Session roles must be train and eval")
    labels={d:{r["image"]:r for r in manifest(d/"labels.jsonl") if r.get("reviewed")} for d in [train,evaluation]}
    rounds=sorted({r["round_id"] for r in labels[train].values() if r.get("round_id") is not None})
    if len(rounds)<2:
        raise ValueError("At least two annotated training rounds are needed for a round-disjoint validation split")
    val_rounds=set(rounds[-max(1,len(rounds)//10):])
    dest=ROOT/"data"/"rank_training.zip"
    if dest.exists():
        raise ValueError("Training archive exists; choose a new version before overwriting")
    hashes={d:{r["sha256"] for r in manifest(d/"frames.jsonl")} for d in [train,evaluation]}
    if hashes[train]&hashes[evaluation]:
        raise ValueError("Identical frames leaked across training/evaluation sessions")
    config={"path":"/content/dataset","train":"train/images","val":"val/images",
            "test":"holdout/images","names":{i:r for i,r in enumerate(RANKS)}}
    provenance={"training_sessions":[train.name],"evaluation_sessions":[evaluation.name],
                "validation_rounds":list(val_rounds),"rank_order":RANKS}
    import yaml
    with zipfile.ZipFile(dest,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.yaml",yaml.safe_dump(config))
        archive.writestr("provenance.json",json.dumps(provenance,indent=2))
        total=0
        for directory in [train,evaluation]:
            for row in manifest(directory/"frames.jsonl"):
                label=labels[directory].get(row["image"])
                if not label or not row.get("redacted"):
                    continue
                source=directory/row["image"]
                image=cv2.imread(str(source)); h,w=image.shape[:2]
                split="holdout" if directory==evaluation else "val" if label["round_id"] in val_rounds else "train"
                name=directory.name+"_"+Path(row["image"]).stem
                encoded=[]
                if any(canonical_rank(c["rank"])=="unknown" for c in label["cards"]):
                    continue # unknown boxes are not silently taught as background
                for card in label["cards"]:
                    l,t,r,b=card["box"]; rank=canonical_rank(card["rank"])
                    encoded.append(f"{RANKS.index(rank)} {(l+r)/2/w:.6f} {(t+b)/2/h:.6f} {(r-l)/w:.6f} {(b-t)/h:.6f}")
                total+=source.stat().st_size
                if total>500*1024**2:
                    raise ValueError("Training archive input exceeds 500 MB; obtain approval before expanding")
                archive.write(source,f"{split}/images/{name}.png")
                archive.writestr(f"{split}/labels/{name}.txt","\n".join(encoded))
    print(json.dumps({"archive":str(dest),"bytes":dest.stat().st_size,"training_sessions":provenance["training_sessions"]}))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--train",required=True); p.add_argument("--eval",required=True)
    prepare(p.parse_args())
