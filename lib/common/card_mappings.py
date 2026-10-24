"""Model class-label -> card-name mappings for the two detection models."""

# Player model (dey022): classes a1..d13 map to full card names.
# a=Hearts, b=Diamonds, c=Spades, d=Clubs; 1=Ace, 11=Jack, 12=Queen, 13=King.
_SUIT_PREFIXES = {"a": "Hearts", "b": "Diamonds", "c": "Spades", "d": "Clubs"}
_RANK_NUMBERS = {
    1: "Ace", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8",
    9: "9", 10: "10", 11: "Jack", 12: "Queen", 13: "King",
}

PLAYER_CLASS_MAP = {
    f"{prefix}{num}": f"{rank} of {suit}"
    for prefix, suit in _SUIT_PREFIXES.items()
    for num, rank in _RANK_NUMBERS.items()
}
# Known stray class emitted by the player model for a worn 8 of Diamonds.
PLAYER_CLASS_MAP["b87"] = "8 of Diamonds"

# Common ONNX/Ultralytics labels; retain suit when available, count by rank.
for _suit_code, _suit_name in {"C":"Clubs","D":"Diamonds","H":"Hearts","S":"Spades"}.items():
    for _rank in ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]:
        _name = {"A":"Ace","J":"Jack","Q":"Queen","K":"King"}.get(_rank,_rank)
        PLAYER_CLASS_MAP[_rank+_suit_code] = f"{_name} of {_suit_name}"
        PLAYER_CLASS_MAP[f"{_name} of {_suit_name}"] = f"{_name} of {_suit_name}"
# A range-only detector can feed the counter and basic strategy directly.
for _rank in ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]:
    PLAYER_CLASS_MAP[_rank] = {"A":"Ace","J":"Jack","Q":"Queen","K":"King"}.get(_rank,_rank)

# Dealer model (carddetection-v1hqz): classes are bare ranks.
DEALER_CLASS_MAP = {
    "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8",
    "9": "9", "10": "10", "J": "Jack", "Q": "Queen", "K": "King", "A": "Ace",
}

# Dealer-model classes that are not cards but table events.
CUTTING_CARD_CLASS = "cuttingcard"
