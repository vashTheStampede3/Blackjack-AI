"""Model-independent rank vocabulary. Unknown is explicit, never a guessed ten."""
import re

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
LONG = {"A": "Ace", "J": "Jack", "Q": "Queen", "K": "King"}


def canonical_rank(label):
    label = str(label).strip()
    legacy = re.fullmatch(r"([abcd])(1[0-3]|[1-9])", label)
    if legacy:
        return {"1": "A", "11": "J", "12": "Q", "13": "K"}.get(legacy[2], legacy[2])
    if label == "b87":
        return "8"
    token = label.upper().replace("_", " ").replace("-", " ")
    token = re.sub(r"\s+OF\s+.*$", "", token)
    token = {"ACE": "A", "JACK": "J", "QUEEN": "Q", "KING": "K", "TEN": "10"}.get(token, token)
    match = re.fullmatch(r"(10|[2-9AJQK])(?:[CDHS♣♦♥♠])?", token)
    return match[1] if match else "unknown"


def bucket(rank):
    rank = canonical_rank(rank)
    return "low" if rank in ("2", "3", "4", "5", "6") else (
        "neutral" if rank in ("7", "8", "9") else "high" if rank != "unknown" else "unknown")


def delta(rank):
    return {"low": 1, "neutral": 0, "high": -1, "unknown": 0}[bucket(rank)]
