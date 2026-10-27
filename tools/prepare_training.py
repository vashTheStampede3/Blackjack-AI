"""Build a private, rank-only Colab dataset with session/round split isolation."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import cv2
import numpy as np

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


# ---------------------------------------------------------------------------
# Synthetic Digital Card Strip Generation (Etapa 8 Dual Specialized Detection)
# ---------------------------------------------------------------------------

def generate_digital_card_sprite(rank, suit="S", width=60, height=90,
                                background=(248, 248, 248), border_color=(40, 40, 40)):
    """Generate a clean 2D card sprite image with rank and suit indications.

    Ranks supported: 2, 3, 4, 5, 6, 7, 8, 9, 10, J, Q, K, A.
    """
    c_rank = canonical_rank(rank)
    if c_rank == "unknown":
        raise ValueError(f"Unknown or invalid rank for sprite generation: {rank}")
    sprite = np.full((height, width, 3), background, dtype=np.uint8)
    # Outer border
    cv2.rectangle(sprite, (0, 0), (width - 1, height - 1), border_color, 1)

    # Red for Hearts/Diamonds, Black for Spades/Clubs
    is_red = str(suit).upper() in ("H", "D", "HEARTS", "DIAMONDS")
    text_color = (0, 0, 180) if is_red else (20, 20, 20)

    # Corner text
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.45 if len(c_rank) > 1 else 0.55
    cv2.putText(sprite, c_rank, (4, 18), font, scale, text_color, 1, cv2.LINE_AA)

    # Center text
    c_scale = 0.7 if len(c_rank) > 1 else 0.85
    (tw, th), _ = cv2.getTextSize(c_rank, font, c_scale, 2)
    tx = max(2, (width - tw) // 2)
    ty = max(th + 4, (height + th) // 2)
    cv2.putText(sprite, c_rank, (tx, ty), font, c_scale, text_color, 2, cv2.LINE_AA)
    return sprite


def generate_digital_card_strip(ranks, suits=None, card_size=(60, 90),
                                spacing=10, overlap_pixels=0,
                                background_color=(35, 35, 35),
                                left_margin=20, top_margin=20):
    """Generate a horizontal strip containing 1 to 5 digital card sprites.

    Args:
        ranks: List of rank strings, e.g. ['A', 'K', '5'] (1 to 5 cards typical).
        suits: Optional list of suit strings corresponding to each card.
        card_size: Tuple (width, height) for each card sprite.
        spacing: Horizontal gap between cards when non-overlapping.
        overlap_pixels: Horizontal overlap between consecutive cards (>0 means overlap).
        background_color: BGR background color tuple.
        left_margin: Left and right margin in pixels.
        top_margin: Top and bottom margin in pixels.

    Returns:
        tuple: (strip_image, annotations)
            strip_image: np.ndarray BGR image of the generated strip.
            annotations: list of dicts with keys:
                rank, suit, box [l, t, r, b], cx, cy, width, height, class_idx
    """
    import numpy as np
    n = len(ranks)
    if n == 0:
        raise ValueError("At least one rank must be provided to generate a digital strip")
    w, h = card_size
    if suits is None:
        suits = ["S"] * n
    elif len(suits) < n:
        suits = list(suits) + ["S"] * (n - len(suits))

    step = (w - overlap_pixels) if overlap_pixels > 0 else (w + spacing)
    total_width = 2 * left_margin + max(0, (n - 1) * step) + w
    total_height = 2 * top_margin + h

    strip = np.full((total_height, total_width, 3), background_color, dtype=np.uint8)
    annotations = []

    for i, (rank_val, suit_val) in enumerate(zip(ranks, suits)):
        c_rank = canonical_rank(rank_val)
        sprite = generate_digital_card_sprite(c_rank, suit=suit_val, width=w, height=h)
        x1 = left_margin + i * step
        y1 = top_margin
        x2 = x1 + w
        y2 = y1 + h
        strip[y1:y2, x1:x2] = sprite

        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        class_idx = RANKS.index(c_rank) if c_rank in RANKS else -1
        annotations.append({
            "rank": c_rank,
            "suit": suit_val,
            "box": [x1, y1, x2, y2],
            "cx": float(cx),
            "cy": float(cy),
            "width": float(w),
            "height": float(h),
            "class_idx": class_idx,
        })

    return strip, annotations


def format_yolo_labels(annotations, image_width, image_height):
    """Format strip annotations into standard normalized YOLO label lines."""
    lines = []
    for ann in annotations:
        c_idx = ann.get("class_idx", 0)
        cx = ann["cx"] / float(image_width)
        cy = ann["cy"] / float(image_height)
        w = ann["width"] / float(image_width)
        h = ann["height"] / float(image_height)
        lines.append(f"{c_idx} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return lines


def generate_synthetic_strip_dataset(num_samples, output_dir=None, min_cards=1,
                                     max_cards=5, card_size=(60, 90),
                                     allow_overlap=True, seed=None):
    """Generate a batch of synthetic digital strips for model training/eval."""
    import random
    if seed is not None:
        random.seed(seed)
    results = []
    out_path = Path(output_dir) if output_dir else None
    if out_path:
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "images").mkdir(exist_ok=True)
        (out_path / "labels").mkdir(exist_ok=True)

    all_ranks = list(RANKS)
    suits = ["S", "H", "D", "C"]

    for idx in range(num_samples):
        n_cards = random.randint(min_cards, max_cards)
        sample_ranks = [random.choice(all_ranks) for _ in range(n_cards)]
        sample_suits = [random.choice(suits) for _ in range(n_cards)]
        overlap = random.randint(5, 18) if allow_overlap and random.random() < 0.5 else 0
        spacing = random.randint(6, 16) if overlap == 0 else 0

        img, anns = generate_digital_card_strip(
            sample_ranks, suits=sample_suits, card_size=card_size,
            spacing=spacing, overlap_pixels=overlap
        )
        h, w = img.shape[:2]
        yolo_lines = format_yolo_labels(anns, w, h)

        if out_path:
            stem = f"digital_strip_{idx:05d}"
            cv2.imwrite(str(out_path / "images" / f"{stem}.png"), img)
            (out_path / "labels" / f"{stem}.txt").write_text("\n".join(yolo_lines), encoding="utf-8")

        results.append({"image": img, "annotations": anns, "yolo_labels": yolo_lines})
    return results


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--train",required=True); p.add_argument("--eval",required=True)
    prepare(p.parse_args())
