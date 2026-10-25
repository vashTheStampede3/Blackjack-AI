"""Rank adapter, privacy boundary, temporal deduplication and shoe properties."""
import random
from types import SimpleNamespace

import numpy as np
import pytest

from lib.logic.vision_labels import RANKS, canonical_rank, delta
from lib.logic.vision_counter import TemporalCounter
from tools.common import change_score, load_config, redact, write_json
from tools.detectors import Detection, group_pips
from tools.eval import score_frames


def pred(rank="2",x=0,confidence=.95):
    return Detection(rank,[x,0,x+20,30],confidence,rank)


@pytest.mark.parametrize("label,rank",[("AS","A"),("10H","10"),("Ace of Spades","A"),
    ("a1","A"),("d13","K"),("b87","8"),("Queen","Q"),("A","A"),("cuttingcard","unknown"),("garbage","unknown")])
def test_normalize(label,rank):
    assert canonical_rank(label)==rank


def test_known_hilo_and_true_count():
    c=TemporalCounter()
    ranks=["2","3","4","5","6","7","8","9","10","J","Q","K","A","2"]
    for frame in range(3):
        c.observe("r1",str(frame),[pred(r,i*35) for i,r in enumerate(ranks)])
    assert c.running==1
    assert c.snapshot()["true"]==pytest.approx(1/(8-14/52))


def test_duplicate_boxes_and_repeated_frames_never_double_count():
    c=TemporalCounter()
    for frame in range(20):
        c.observe("r1",str(frame),[pred(),pred(),pred()])
        c.observe("r1",str(frame),[pred(),pred()])
    assert c.cards_seen==1
    assert c.running==1


def test_adjacent_identical_cards_survive():
    c=TemporalCounter()
    for frame in range(5):
        c.observe("r1",str(frame),[pred("K",0),pred("K",15)])
    assert c.cards_seen==2
    assert c.running==-2


def test_confusion_remains_unknown_until_consensus():
    c=TemporalCounter()
    for i,rank in enumerate(["6","8","6","8","6","8"]):
        c.observe("r1",str(i),[pred(rank)])
    assert c.cards_seen==0
    assert c.snapshot()["unknown"]==1


def test_old_outliers_expire_without_reducing_confirmation_threshold():
    c=TemporalCounter()
    for i,rank in enumerate(["8","9","6","6","8","6","6","6"]):
        c.observe("r1",str(i),[pred(rank)])
    assert c.cards_seen==1 and c.running==1
    assert c.tracks[0].committed=="6"


def test_one_late_outlier_does_not_taint_confirmed_card():
    c=TemporalCounter(origin_known=True)
    for i,rank in enumerate(["6","6","6","8","6","6","6"]):
        c.observe("r1",str(i),[pred(rank)])
    assert c.cards_seen==1 and c.running==1
    assert c.snapshot()["count_valid"]


def test_sustained_contradiction_becomes_unknown_without_recount():
    c=TemporalCounter(origin_known=True)
    for i,rank in enumerate(["6","6","6","8","8","8","8"]):
        c.observe("r1",str(i),[pred(rank)])
    assert c.cards_seen==1 and c.running==1
    assert c.snapshot()["unknown"]==1
    assert not c.snapshot()["count_valid"]


def test_reordered_replay_invariant():
    frames=[{"timestamp":i,"frame_id":str(i),"round_id":i//4,"detections":[pred("A" if i//4 else "3")]} for i in range(8)]
    a=TemporalCounter().replay(frames)
    random.Random(13).shuffle(frames)
    b=TemporalCounter().replay(frames)
    assert a==b and a["cards_seen"]==2


def test_revisit_old_round_does_not_count_again():
    c=TemporalCounter()
    for rid in [1,2,1]:
        for f in range(4):
            c.observe(rid,str(f),[pred()])
    assert c.cards_seen==2


def test_manual_shuffle_fallback_and_cut_card_not_reset():
    c=TemporalCounter()
    for frame in range(3): c.observe(1,str(frame),[pred()])
    with pytest.raises(ValueError): c.reset_shoe("cutting_card")
    assert c.cards_seen==1
    c.reset_shoe("manual")
    assert c.cards_seen==c.running==0 and c.shoe==1


def test_three_shoes_manual_fallback_and_unknown_origin():
    c=TemporalCounter()
    assert not c.snapshot()["count_valid"]
    for shoe in range(3):
        c.reset_shoe("manual")
        assert c.snapshot()["count_valid"]
        for frame in range(3): c.observe(1,str(frame),[pred("A")])
        assert c.running==-1 and c.cards_seen==1
    assert c.shoe==3


def test_occlusion_anchor_guard(tmp_path,monkeypatch):
    import cv2
    import tools.common as common
    image=np.zeros((40,40,3),np.uint8)
    image[10:30,10:30]=np.random.default_rng(3).integers(0,256,(20,20,3),dtype=np.uint8)
    cv2.imwrite(str(tmp_path/"anchor.png"),image[10:30,10:30])
    monkeypatch.setattr(common,"ROOT",tmp_path)
    cfg={"anchors":[{"rect":[10,10,30,30],"image":"anchor.png"}]}
    assert common.table_anchors_match(image,cfg)
    assert not common.table_anchors_match(np.zeros_like(image),cfg)


def test_full_eight_deck_shoe_and_capacity():
    c=TemporalCounter()
    deck=list(RANKS)*32
    for i,rank in enumerate(deck):
        for frame in range(3): c.observe(i,str(frame),[pred(rank)])
    assert c.cards_seen==416 and c.running==0
    for frame in range(3): c.observe(999,str(frame),[pred("A")])
    assert c.cards_seen==416


def test_pips_same_code_only_and_no_transitive_chain():
    p=[Detection("A",[x,0,x+4,8],.9,"AS") for x in [0,12,24,36]]
    assert len(group_pips(p,16))==2
    p[1].code="AH"
    assert len(group_pips(p[:2],16))==2


def test_masks_before_persistence_and_small_card_change():
    image=np.full((96,160,3),255,np.uint8)
    redacted=redact(image,{"masks":[[0,.8,1,1]]})
    assert not redacted[80:].any() and image[80:].all()
    previous,_=change_score(None,redacted)
    redacted[20:28,20:28]=0
    _,score=change_score(previous,redacted)
    assert score>1


def test_unsafe_capture_configuration_rejected(tmp_path):
    cfg={"roi":[0,0,100,100],"geometry_verified":False,"privacy_reviewed":False}
    write_json(tmp_path/"table.json",cfg)
    with pytest.raises(ValueError): load_config(tmp_path/"table.json")


def test_evaluator_counts_misses_confusions_and_false_positives():
    truth=[{"rank":"A","box":[0,0,20,30]},{"rank":"2","box":[30,0,50,30]}]
    result=score_frames([(truth,[pred("K"),pred("3",100)])])
    assert result["ground_truth_observations"]==2
    assert result["rank_accuracy"]==0
    assert result["bucket_accuracy"]==.5
    assert result["confusion"]["2"]["MISS"]==1
    assert result["confusion"]["FP"]["3"]==1


def test_unlabelled_is_not_zero_or_perfect_accuracy():
    result=score_frames([])
    assert result["rank_accuracy"] is None
    assert all(row["recall"] is None for row in result["per_rank"].values())
