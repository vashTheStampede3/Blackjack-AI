"""Replay a local video through DetectionEngine; save numeric evidence, never images.

The ONNX detector, evidence gates, card interpretation and counting are real.
Only unrelated financial advice, OCR, training, persistence and remote services
are disabled. Labels delimit scoring and never enter the recognition engine.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import socket
import time

import cv2

from lib.common import constants, settings
from lib.logic import region_profiles
from lib.logic.card_layout import SEVEN_SEATS
from lib.logic.counting import CardCounter
from lib.logic.engine import DetectionEngine
from lib.logic.models import ManualShuffleModel, OnnxModel
from lib.logic.video_source import VideoFile


@contextmanager
def no_network():
    """Fail closed even if an optional imported component tries to connect."""
    previous = socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo
    def denied(*args, **kwargs):
        raise RuntimeError("Network is forbidden during local replay")
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = socket.getaddrinfo = denied
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = previous


class ReplaySource:
    """Current wall-clock position; slow inference drops frames as live capture does."""
    def __init__(self, reader, start, end, clock=time.monotonic):
        self.reader, self.start, self.end, self.clock = reader, start, end, clock
        self.width, self.height = reader.resolution
        self.origin = None
        self.timestamp = start
        self.frame_index = 0
        self.ended = False
        self.target_frame = None

    def begin(self):
        self.origin = self.clock()

    def grab_bgr(self):
        seconds = (self.target_frame / self.reader.fps if self.target_frame is not None
                   else self.start + self.clock() - self.origin)
        if seconds >= self.end:
            raise EOFError("End of replay")
        try:
            source = self.reader.frame_at(seconds)
        except ValueError:
            # Some files report one final frame which the decoder cannot return.
            # An earlier decoding error remains an error, never a successful run.
            if seconds >= self.reader.last_frame_seconds - 1 / self.reader.fps:
                self.ended = True
                raise EOFError("End of decodable video") from None
            raise
        self.timestamp, self.frame_index = source.timestamp_seconds, source.frame_index
        if self.target_frame is not None and self.frame_index != self.target_frame:
            raise ValueError("Decoder did not return the exact scheduled frame")
        return source.grab_bgr()


class AuditedModel:
    hosted = False
    name = "Local ONNX replay"

    def __init__(self, model):
        self.model = model
        self.calls = []

    def predict(self, image, confidence, overlap):
        t0 = time.perf_counter()
        predictions = self.model.predict(image, confidence, overlap)
        h, w = image.shape[:2]
        sx = sy = self.model._size / max(w, h)
        if self.model._resize_mode == "Stretch to":
            sx, sy = self.model._size / w, self.model._size / h
        self.calls.append({"input_size": [w, h], "model_size": self.model._size,
                           "ms": (time.perf_counter() - t0) * 1000,
                           "predictions": [p | {"model_box_px": [p["width"]*sx, p["height"]*sy]}
                                           for p in predictions]})
        return predictions


class LocalProvider:
    def __init__(self, model):
        self.model = model
        self.backend_name = "Local ONNX CPU (explicit, no fallback)"

    def players_model(self):
        return self.model

    def dealer_model(self):
        return ManualShuffleModel()


class TraceCounter(CardCounter):
    def __init__(self, engine, source):
        super().__init__(deck_count=constants.DECK_COUNT)
        self.engine, self.source, self.events = engine, source, []
        self.known = set()

    def count_card(self, name):
        super().count_card(name)
        owner, x, y, identity = None, None, None, None
        candidates = []
        for seat in self.engine.seats:
            for i, card in enumerate(seat.cards):
                candidates.append(((self.engine.round_number, seat.index, i), seat.index,
                                   card["name"], card.get("cx"), card.get("cy")))
        if self.engine.dealer_card:
            pos = self.engine._dealer_pos or (None, None)
            candidates.append(((self.engine.round_number, "dealer", 0), "dealer", self.engine.dealer_card, *pos))
        for i, card in enumerate(self.engine.dealer_extras, 1):
            candidates.append(((self.engine.round_number, "dealer", i), "dealer", card["rank"], card.get("cx"), card.get("cy")))
        for key, candidate_owner, rank, cx, cy in candidates:
            if key not in self.known and rank == name:
                self.known.add(key)
                owner, x, y, identity = candidate_owner, cx, cy, key
                break
        self.events.append({"kind": "count", "seconds": self.source.timestamp,
                            "round": self.engine.round_number, "owner": owner, "identity": identity,
                            "rank": name, "cx": x, "cy": y, "running": self.running_count,
                            "cards_seen": self.cards_seen})


class ReplayEngine(DetectionEngine):
    """Keep production recognition; skip calculations unrelated to this measurement."""
    def _optimal_advice(self, *args, **kwargs):
        return "", constants.ACTION_COLORS["-"], None

    def _warm_advice_job(self, *args):
        pass

    def _predeal_edge(self, *args):
        return None

    def _side_bet_evs(self, *args):
        return []

    def _settle_round(self, *args):
        return None


def validate_labels(labels):
    from lib.logic.cards import hilo_delta
    aliases = {"A": "Ace", "J": "Jack", "Q": "Queen", "K": "King"}
    for row in labels["rounds"]:
        if len(row["cards"]) != row["expected_cards"]:
            raise ValueError("The label card total is inconsistent")
        actual = sum(hilo_delta(aliases.get(c["rank"], c["rank"])) for c in row["cards"])
        if actual != row["expected_hilo_delta"]:
            raise ValueError("The label Hi-Lo sum is inconsistent")


def summarize(events, labels):
    boundaries = [row.get("user_start_seconds", row.get("user_deal_interval_seconds", [0])[0])
                  for row in labels["rounds"]]
    rows = []
    for i, row in enumerate(labels["rounds"]):
        end = boundaries[i+1] if i+1 < len(boundaries) else float("inf")
        observed = [e for e in events if boundaries[i] <= e["seconds"] < end and e["kind"] == "count"]
        before = [e for e in events if e["seconds"] < boundaries[i] and e["kind"] == "count"]
        initial = before[-1]["running"] if before else 0
        running = observed[-1]["running"] if observed else initial
        rows.append({"round": row["round"], "counted": len(observed), "expected": row["expected_cards"],
                     "delta": running-initial, "expected_delta": row["expected_hilo_delta"],
                     "cumulative": running, "expected_cumulative": row["expected_cumulative_running_count"]})
    return rows


def run(args):
    labels_bytes = Path(args.labels).read_bytes()
    labels = json.loads(labels_bytes)
    validate_labels(labels)
    scope = labels["evaluation_scope"]
    schedule = ([json.loads(line) for line in Path(args.schedule).read_text(encoding="utf-8").splitlines()]
                if args.schedule else None)
    model_path = Path(args.model)
    target = Path(args.output)
    if target.exists():
        raise FileExistsError("Choose a new output directory; existing evidence is never overwritten")
    settings.load_and_apply()
    for cfg in (constants.OCR, constants.PHASE, constants.ANCHORS, constants.TRAINING, constants.VISION):
        cfg["enabled"] = 0
    constants.EXECUTOR["mode"] = "off"
    constants.BETTING["auto_bankroll"] = constants.BETTING["use_exact_edge"] = 0
    cv2.setNumThreads(2)
    with no_network(), VideoFile(labels["video_path"]) as reader:
        source = ReplaySource(reader, scope["start_seconds"], min(scope["end_seconds"], reader.duration_seconds))
        audited = AuditedModel(OnnxModel(model_path))
        engine = ReplayEngine(log=lambda *a, **k: None, provider=LocalProvider(audited), persist=False)
        engine.set_monitor(source, profile=args.profile)
        if args.layout_profile:
            layout = region_profiles.get_card_layout(reader.resolution, "table", profile=args.layout_profile)
            if layout is None:
                raise ValueError("The selected table profile has no exact-resolution layout")
            reference = layout.reference_source or {}
            if reference.get("path") and Path(reference["path"]).resolve() != Path(labels["video_path"]).resolve():
                raise ValueError("The table profile was calibrated on a different video; review it first")
            engine.use_card_layout(layout, memory=args.memory)
        engine.counter = TraceCounter(engine, source)
        records = []
        target.mkdir(parents=True)
        metadata = {"profile": args.profile, "layout_profile": args.layout_profile,
                    "layout_mode": engine.card_layout.mode if engine.card_layout else SEVEN_SEATS, "memory": args.memory,
                    "video_resolution": list(reader.resolution), "fps": reader.fps,
                    "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                    "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                      (Path(__file__), Path("lib/logic/engine.py"), Path("lib/logic/table_inference.py"))},
                    "labels_sha256": hashlib.sha256(labels_bytes).hexdigest(),
                    "schedule": args.schedule, "exact_comparison_frames": bool(schedule),
                    "scope": scope, "network": False, "inference_threads": 2,
                    "threshold": constants.PREDICTION_CONFIDENCE_PLAYERS,
                    "exact_visibility_times": False, "recognition": "real local ONNX",
                    "disabled": ["financial EV", "OCR", "phase", "anchors", "training", "session writes"]}
        (target / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        source.begin()
        next_progress = source.start
        schedule_index = 0
        completed = False
        failure = None
        try:
            with (target / "cycles.jsonl").open("w", encoding="utf-8") as trace:
                while schedule_index < len(schedule) if schedule is not None else source.start + time.monotonic() - source.origin < source.end:
                    if schedule is not None:
                        target_frame = schedule[schedule_index]
                        delay = target_frame["seconds"]-source.start-(time.monotonic()-source.origin)
                        if delay > 0:
                            time.sleep(delay)
                        source.target_frame = target_frame["frame"]
                        schedule_index += 1
                    activity = engine.run_cycle()
                    if engine.last_error:
                        if source.ended:
                            completed = True
                            break
                        raise RuntimeError(engine.last_error)
                    record = {"seconds": source.timestamp, "frame": source.frame_index,
                              "wall_lag_seconds": max(0., source.start+time.monotonic()-source.origin-source.timestamp),
                              "snapshot": engine.get_snapshot(), "model_calls": audited.calls}
                    audited.calls = []
                    trace.write(json.dumps(record) + "\n")
                    trace.flush()
                    records.append(record)
                    if source.timestamp >= next_progress:
                        print(f'{source.timestamp:.1f}s: {engine.counter.cards_seen} cards, RC {engine.counter.running_count:+d}', flush=True)
                        next_progress = source.timestamp + 30
                    delay = {"dealing": constants.CYCLE_SLEEP_DEALING,
                             "complete": constants.CYCLE_SLEEP_COMPLETE,
                             "waiting": constants.CYCLE_SLEEP_WAITING}[activity]
                    if schedule is None:
                        time.sleep(min(delay, max(0., source.end-source.start-(time.monotonic()-source.origin))))
                else:
                    completed = True
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
                getattr(engine, name).shutdown(wait=True, cancel_futures=True)
            events = engine.counter.events
            result = {"rounds": summarize(events, labels), "events": events,
                      "final_count": engine.counter.snapshot(), "cycles": len(records),
                      "completed": completed, "error": failure,
                      "reading": engine._round_observation.snapshot()}
            (target / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k in ("rounds", "cycles", "completed")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--layout-profile", help="Optional three-zone profile; seven-seat profile remains the legacy-memory reference")
    parser.add_argument("--memory", choices=["legacy"], default="legacy")
    parser.add_argument("--model", default=str(constants.MODELS_DIR / "player_cards.onnx"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--schedule", help="Existing replay cycles: compare the exact same frames at their original times")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
