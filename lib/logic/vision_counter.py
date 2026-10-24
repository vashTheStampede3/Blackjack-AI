"""Measured rank-only counter candidate; never promoted by a synthetic pass.

Distinct frames vote on persistent physical tracks. Ambiguous tracks remain
unknown. Replaying out-of-order input first sorts original capture timestamps.
"""
from collections import Counter
from dataclasses import dataclass, field
import math

from .vision_labels import canonical_rank, delta


@dataclass
class Track:
    identity: str
    box: list
    votes: Counter = field(default_factory=Counter)
    committed: str | None = None
    conflicting: bool = False

    @property
    def rank(self):
        return self.committed or "unknown"


class TemporalCounter:
    def __init__(self, decks=8, confirmations=3, consensus=.7, min_confidence=.5, origin_known=False):
        self.decks = decks
        self.confirmations = confirmations
        self.consensus = consensus
        self.min_confidence = min_confidence
        self.running = 0
        self.cards_seen = 0
        self.events = []
        self.round_id = None
        self.tracks = []
        self._frames = set()
        self.shoe = 0
        self.origin_known = origin_known
        self._round_tracks = {}
        self._round_frames = {}
        self._rank_seen = Counter()

    def start_round(self, round_id):
        if round_id != self.round_id:
            self.round_id = round_id
            self.tracks = self._round_tracks.setdefault(round_id, [])
            self._frames = self._round_frames.setdefault(round_id, set())

    def reset_shoe(self, reason="manual"):
        if reason not in ("manual", "confirmed_shuffle"):
            raise ValueError("A cutting card alone does not prove a shuffle occurred")
        self.running = self.cards_seen = 0
        self.shoe += 1
        self.origin_known = True
        self.tracks = []
        self._frames = set()
        self.round_id = None
        self._round_tracks.clear()
        self._round_frames.clear()
        self._rank_seen.clear()
        self.events.append({"type":"shoe_reset","reason":reason,"shoe":self.shoe})

    @staticmethod
    def distance(box, previous):
        ax,ay = (box[0]+box[2])/2,(box[1]+box[3])/2
        bx,by = (previous[0]+previous[2])/2,(previous[1]+previous[3])/2
        scale = max(4., min(max(box[2]-box[0],box[3]-box[1]),
                            max(previous[2]-previous[0],previous[3]-previous[1]))*.4)
        return math.hypot(ax-bx,ay-by)/scale

    def observe(self, round_id, frame_id, detections):
        self.start_round(round_id)
        if frame_id in self._frames:
            return []
        self._frames.add(frame_id)
        accepted = []
        available = set(range(len(self.tracks)))
        # One physical track can consume at most one box in a frame, allowing
        # simultaneous adjacent copies of the same rank in an eight-deck shoe.
        unique = []
        for pred in sorted(detections,key=lambda p:p.confidence,reverse=True):
            if not any(self.distance(pred.box,p.box)<.3 for p in unique):
                unique.append(pred)
        for pred in sorted(unique,key=lambda p:(p.box[0],p.box[1])):
            distances = [(self.distance(pred.box,self.tracks[i].box),i) for i in available]
            best = min(distances,default=(float("inf"),None))
            if best[0] <= 1:
                index = best[1]
                available.remove(index)
                track = self.tracks[index]
                track.box = list(pred.box)
            else:
                track = Track(f"{self.shoe}:{round_id}:{len(self.tracks)}",list(pred.box))
                self.tracks.append(track)
            rank = canonical_rank(pred.rank)
            if pred.confidence < self.min_confidence or rank == "unknown":
                continue
            track.votes[rank] += 1
            winner, count = track.votes.most_common(1)[0]
            if track.committed:
                if rank != track.committed:
                    track.conflicting = True
                continue
            if count < self.confirmations or count/sum(track.votes.values()) < self.consensus:
                continue
            if self._rank_seen[winner] >= 4*self.decks:
                track.conflicting = True
                continue
            track.committed = winner
            self._rank_seen[winner] += 1
            self.running += delta(winner)
            self.cards_seen += 1
            event = {"type":"card","card_id":track.identity,"round_id":round_id,
                     "rank":winner,"delta":delta(winner),"running":self.running,
                     "frame_id":frame_id,"shoe":self.shoe}
            self.events.append(event)
            accepted.append(event)
        return accepted

    def snapshot(self):
        remaining = max(0.,self.decks-self.cards_seen/52)
        unknown = sum(t.committed is None or t.conflicting for t in self.tracks)
        return {"running":self.running,"cards_seen":self.cards_seen,
                "decks_remaining":remaining,"true":self.running/remaining if remaining else None,
                "unknown":unknown,"count_valid":self.origin_known and unknown==0,
                "shoe_origin_known":self.origin_known}

    def replay(self, frames):
        for frame in sorted(frames,key=lambda f:(f["timestamp"],f["frame_id"])):
            self.observe(frame["round_id"],frame["frame_id"],frame["detections"])
        return self.snapshot()
