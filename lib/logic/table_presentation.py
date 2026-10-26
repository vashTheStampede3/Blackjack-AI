"""Pure, detached UI data from the engine's published card groups.

No legacy ``seats`` advice is transferred: the snapshot publishes no verified
association between that advice and a physical group. ``id`` is the logical
``hero`` in Individual and the unchanged ``seat_id`` in Multiplayer. Optional
source metadata and legacy ``slot_index`` are copied, never inferred. Card
positions, timestamps and legacy references remain available to future views.

Readiness describes the published reading, not its age. This module has no
clock and cannot certify temporal freshness or calibration geometry.
"""

from collections.abc import Mapping
from copy import deepcopy

from .app_modes import APP_MODES, INDIVIDUAL


PRESENTATION_STATES = (
    "stopped", "waiting", "reading", "uncertain", "ready", "error",
    "needs_calibration", "source_mismatch",
)

_MESSAGES = {
    "stopped": "Detección detenida",
    "waiting": "Esperando cartas",
    "reading": "Lectura incompleta",
    "uncertain": "Lectura dudosa",
    "ready": "Mano lista",
    "error": "Error de captura o detección",
    "needs_calibration": "Seleccioná un modo válido para presentar la mesa",
    "source_mismatch": "El snapshot pertenece a otro modo",
}
_CARD_FIELDS = (
    "card_id", "rank", "suit", "state", "owner", "hand", "legacy_ref",
    "reason", "center_norm", "bbox_norm", "ambiguous_seat", "first_seen",
    "last_seen", "bbox", "cx", "cy", "confidence",
)
_SOURCE_FIELDS = (
    "source_mode", "source_profile", "source_resolution", "source_backend",
    "source_context", "dealer_source",
)


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _identifier(value):
    """Keep only meaningful scalar metadata, without coercing identities."""
    if isinstance(value, str) and value.strip():
        return value
    if type(value) is int and value >= 0:
        return value
    return None


def _records(value, name, issues):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        issues.append(f"{name}: se esperaba una lista")
        return []
    records = []
    for record in value:
        if isinstance(record, Mapping):
            records.append(record)
        else:
            issues.append(f"{name}: entrada inválida")
    return records


def _normalize_card(card):
    result = {key: deepcopy(card[key]) for key in _CARD_FIELDS if key in card}
    result["rank"] = (deepcopy(card.get("rank"))
                      if card.get("state") == "confirmed" and not card.get("reason")
                      else None)
    result["suit"] = deepcopy(card.get("suit")) or None
    return result


def _player(group=None, *, individual=False, issues):
    group = group or {}
    cards = _records(group.get("cards"), "cards", issues)
    result = {
        "id": "hero" if individual else deepcopy(group.get("seat_id")),
        "seat_id": deepcopy(group.get("seat_id")),
        "label": "Yo" if individual or group.get("is_hero") else deepcopy(group.get("seat_id")),
        "is_hero": True if individual else bool(group.get("is_hero")),
        "is_ambiguous": bool(group.get("is_ambiguous")),
        "has_split": bool(group.get("has_split")),
        "closed_by_double": bool(group.get("closed_by_double")),
        "cards": [_normalize_card(card) for card in cards],
        "advice": None,
    }
    if "slot_index" in group:
        result["slot_index"] = deepcopy(group["slot_index"])
    if individual:
        # An explicit foreign owner must never become our card. Missing owner
        # is normal in the engine's legacy path; is_hero is its published link.
        result["cards"] = [card for card in result["cards"]
                           if card.get("owner") in (None, "mine")]
    return result


def _card_doubts(card, issues):
    if card.get("reason"):
        issues.append(f"Carta {card.get('card_id', card.get('legacy_ref'))}: {card['reason']}")
    if card.get("ambiguous_seat") or card.get("state") in ("doubtful", "uncertain", "rejected", "retired"):
        issues.append("Carta con identidad, asiento o estado dudoso")
    if card.get("owner") not in (None, "mine", "other", "dealer"):
        issues.append("Carta con propietario desconocido")
    if "hand" in card and card["hand"] is None and card.get("owner") == "mine":
        issues.append("Carta propia sin mano identificada")


def _build_players(groups, mode, issues):
    heroes = [group for group in groups if group.get("is_hero")]
    if len(heroes) > 1:
        issues.append("Múltiples grupos propios incompatibles: "
                      + ", ".join(str(group.get("seat_id")) for group in heroes))
    for group in groups:
        if group.get("is_ambiguous"):
            issues.append(f"Grupo ambiguo: {group.get('seat_id')}")
        for card in _records(group.get("cards"), "cards", issues):
            _card_doubts(card, issues)
            owner = card.get("owner")
            if mode == INDIVIDUAL and owner in ("other", "dealer"):
                issues.append("Carta ajena en modo Individual")
            elif owner == "dealer":
                issues.append("Carta del crupier en un grupo de jugadores")
            if ((group.get("is_hero") and owner == "other")
                    or (not group.get("is_hero") and owner == "mine")):
                issues.append("La propiedad de la carta contradice is_hero")
    if mode == INDIVIDUAL:
        player = _player(heroes[0] if len(heroes) == 1 else None,
                         individual=True, issues=issues)
        if len(heroes) > 1:
            player["is_ambiguous"] = True
        return [player]
    if len(groups) > 7:
        issues.append(f"Exceso de jugadores: {len(groups)} grupos; máximo 7")
    seen = []
    for group in groups:
        seat_id = group.get("seat_id")
        if _identifier(seat_id) is None or seat_id in seen:
            issues.append("Identificador de grupo ausente o duplicado")
        seen.append(seat_id)
    # Keep publication order. Excess groups are reported, never accepted as an
    # eighth player or silently merged into another hand.
    return [_player(group, issues=issues) for group in groups[:7]]


def _complete_player(player):
    cards = player["cards"]
    if len(cards) < 2:
        return False
    if player["has_split"]:
        # Only inspect existing hand tags; never discover or regroup a split.
        hands = {}
        for card in cards:
            hand = card.get("hand")
            if type(hand) is not int or hand < 0:
                return False
            hands[hand] = hands.get(hand, 0) + 1
        return len(hands) >= 2 and all(size >= 2 for size in hands.values())
    return True


def _reading_status(snapshot, groups, players, dealer, issues):
    reading = _mapping(snapshot.get("reading"))
    if (_mapping(snapshot.get("count")).get("count_uncertain")
            or reading.get("count_uncertain")):
        issues.append("El snapshot informa incertidumbre en el conteo")
    # The registry can retain explicit ownership/identity conflicts that the
    # display cards omit. It is evidence only, never a second hand source.
    registry_pending = False
    for card in _records(snapshot.get("card_registry"), "card_registry", issues):
        if card.get("state") in ("rejected", "retired") or card.get("status") in ("rejected", "retired"):
            continue
        _card_doubts(card, issues)
        registry_pending |= (card.get("state") != "confirmed"
                             or ("counted" in card and not card["counted"]))
        if card.get("possible_ids"):
            issues.append("El registro informa identidades alternativas para una carta")
        if (card.get("observed_owner") is not None
                and card.get("owner") != card["observed_owner"]):
            issues.append("Propiedad observada distinta de la registrada")
    if issues:
        return "uncertain"
    cards = dealer + [card for player in players for card in player["cards"]]
    observed = bool(cards or groups or reading.get("present"))
    if not observed:
        return "reading" if reading.get("count_pending") or registry_pending else "waiting"
    if reading.get("count_pending") or registry_pending:
        return "reading"
    # No wall clock: explicitly incomplete published evidence is honored, but
    # timestamps cannot be compared with the current time here.
    if any(key in reading and not reading[key]
           for key in ("inferred_at", "dealer_ok", "present")):
        return "reading"
    if (not dealer or not any(player["is_hero"] for player in players)
            or not all(_complete_player(player) for player in players)
            or any(card.get("state") != "confirmed" or not card.get("rank")
                   for card in cards)):
        return "reading"
    return "ready"


def build_table_presentation(snapshot, *, selected_mode=None, running=False) -> dict:
    """Build independent presentation data without mutating or consulting state.

    Precedence: stopped, explicit mode mismatch, invalid mode, source/runtime
    error, uncertainty, incomplete reading, ready. A missing snapshot with a
    valid selected mode produces an empty waiting table. Missing source metadata
    alone is not proof of invalid calibration; only the published mode is checked.
    Unknown modes are never converted into Multiplayer, including seven_seats.
    """
    snap = _mapping(snapshot)
    source_mode = snap.get("source_mode")
    requested = selected_mode if selected_mode is not None else source_mode
    mode = requested if isinstance(requested, str) and requested in APP_MODES else None
    issues = []
    result = {
        "mode": mode,
        "status": "waiting",
        "message": _MESSAGES["waiting"],
        "seq": snap.get("seq") if type(snap.get("seq")) is int and snap["seq"] >= 0 else None,
        "round_id": snap.get("round") if type(snap.get("round")) is int and snap["round"] >= 0 else None,
        "shoe_id": _identifier(snap.get("shoe_id")),
        "dealer": {"cards": []},
        "players": [_player(individual=True, issues=issues)] if mode == INDIVIDUAL else [],
        "advice_available": False,
        "issues": issues,
    }
    for key in _SOURCE_FIELDS:
        if key in snap:
            result[key] = deepcopy(snap[key])
    if not running:
        status = "stopped"
    elif selected_mode is not None and source_mode is not None and source_mode != selected_mode:
        status = "source_mismatch"
        issues.append("source_mode no coincide con selected_mode")
    elif mode is None or (source_mode is not None and source_mode not in APP_MODES):
        status = "needs_calibration"
        issues.append("Modo ausente o desconocido; se requiere una configuración válida")
    elif snap.get("error") or snap.get("source_error"):
        status = "error"
        for key in ("error", "source_error"):
            if snap.get(key):
                issues.append(f"{key}: {snap[key]}")
    else:
        if snapshot is not None and not isinstance(snapshot, Mapping):
            issues.append("Snapshot inválido")
        groups = _records(snap.get("card_groups"), "card_groups", issues)
        result["players"] = _build_players(groups, mode, issues)
        dealer_records = _records(snap.get("dealer_cards"), "dealer_cards", issues)
        for card in dealer_records:
            _card_doubts(card, issues)
            if card.get("owner") not in (None, "dealer"):
                issues.append("Carta ajena en la mano del crupier")
        result["dealer"]["cards"] = [_normalize_card(card) for card in dealer_records]
        status = _reading_status(snap, groups, result["players"], result["dealer"]["cards"], issues)
    result["status"] = status
    result["message"] = _MESSAGES[status]
    return result
