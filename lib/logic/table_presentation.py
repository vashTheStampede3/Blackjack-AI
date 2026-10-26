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


def _normalize_card(card, *, identity_context=None, group_id=None):
    result = {key: deepcopy(card[key]) for key in _CARD_FIELDS if key in card}
    result["rank"] = (deepcopy(card.get("rank"))
                      if card.get("state") == "confirmed" and not card.get("reason")
                      else None)
    result["suit"] = deepcopy(card.get("suit")) or None
    if identity_context is not None:
        result.update(deepcopy(identity_context))
        result["group_id"] = deepcopy(group_id)
    return result


def _player(group=None, *, individual=False, issues, identity_context=None):
    group = group or {}
    cards = _records(group.get("cards"), "cards", issues)
    group_id = "hero" if individual else deepcopy(group.get("seat_id"))
    hand_tags = {card.get("hand") for card in cards
                 if type(card.get("hand")) is int and card.get("hand") >= 0}
    result = {
        "id": "hero" if individual else deepcopy(group.get("seat_id")),
        "seat_id": deepcopy(group.get("seat_id")),
        "group_id": deepcopy(group.get("seat_id")) if not individual else "hero",
        "label": "Yo" if individual or group.get("is_hero") else deepcopy(group.get("seat_id")),
        "is_hero": True if individual else bool(group.get("is_hero")),
        "is_ambiguous": bool(group.get("is_ambiguous")),
        "is_provisional": bool(not individual and isinstance(group.get("seat_id"), str)
                                and group["seat_id"].startswith("TMP_")),
        "has_split": bool(group.get("has_split") or len(hand_tags) > 1),
        "closed_by_double": bool(group.get("closed_by_double")),
        "cards": [_normalize_card(card, identity_context=identity_context,
                                   group_id=group_id) for card in cards],
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


def _build_players(groups, mode, issues, identity_context=None):
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
                         individual=True, issues=issues,
                         identity_context=identity_context)
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
    players = [_player(group, issues=issues, identity_context=identity_context)
               for group in groups[:7]]
    if len(heroes) > 1:
        for player in players:
            if player.get("is_hero"):
                player["is_ambiguous"] = True
    return players


def _identity_context(snapshot, result, mode):
    source_context = _mapping(snapshot.get("source_context"))
    resolution = deepcopy(result.get("source_resolution"))
    profile_name = deepcopy(result.get("source_profile"))
    source_ref = {
        "profile_name": deepcopy(profile_name),
        "mode": mode,
        "resolution": deepcopy(resolution),
        "source_context": deepcopy(snapshot.get("source_context")),
    }
    return {
        "round_id": deepcopy(result.get("round_id")),
        "shoe_id": deepcopy(result.get("shoe_id")),
        "profile_name": profile_name,
        "mode": mode,
        "resolution": resolution,
        "source_id": _identifier(source_context.get("source_id")),
        "source_ref": source_ref,
    }


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


def _attach_physical_advice(result, snapshot):
    """Attach only engine advice bound to this exact physical publication."""
    count = _mapping(snapshot.get("count"))
    reading = _mapping(snapshot.get("reading"))
    raw_groups = snapshot.get("card_groups")
    raw_groups = raw_groups if isinstance(raw_groups, (list, tuple)) else []
    raw_dealer = snapshot.get("dealer_cards")
    raw_dealer = raw_dealer if isinstance(raw_dealer, (list, tuple)) else []
    pending = bool(count.get("count_uncertain") or reading.get("count_uncertain")
                   or reading.get("count_pending"))
    for group in raw_groups:
        if not isinstance(group, Mapping):
            continue
        pending |= (bool(group.get("is_ambiguous"))
                    or str(group.get("seat_id", "")).startswith("TMP_"))
        group_cards = group.get("cards")
        if not isinstance(group_cards, (list, tuple)):
            pending |= bool(group_cards)
            continue
        pending |= any(isinstance(card, Mapping) and (
            card.get("state") != "confirmed" or card.get("reason")
            or card.get("ambiguous_seat")
        ) for card in group_cards)
    pending |= any(isinstance(card, Mapping) and (
        card.get("state") != "confirmed" or card.get("reason")
        or card.get("ambiguous_seat")
    ) for card in raw_dealer)
    result["count_has_pending_uncertainty"] = bool(pending)
    result["insurance"] = None
    result["advice_available"] = False
    payload = _mapping(snapshot.get("physical_advice"))
    same_context = (
        type(result.get("round_id")) is int
        and result.get("round_id") == payload.get("round_id")
        and isinstance(result.get("shoe_id"), str)
        and result.get("shoe_id") == payload.get("shoe_id")
    )
    if not same_context or result.get("status") in (
            "stopped", "error", "needs_calibration", "source_mismatch"):
        return

    result["count_has_pending_uncertainty"] = bool(
        result["count_has_pending_uncertainty"]
        or payload.get("count_has_pending_uncertainty"))
    insurance = payload.get("insurance")
    if (isinstance(insurance, Mapping)
            and ((insurance.get("status") == "ready"
                  and type(insurance.get("take")) is bool)
                 or insurance.get("status") == "no_advice")
            and isinstance(insurance.get("text"), str)):
        result["insurance"] = deepcopy(insurance)
    hero_advice = _mapping(payload.get("hero"))
    if hero_advice.get("identified") is not True:
        return
    group_id = hero_advice.get("group_id")
    physical_ids = hero_advice.get("card_ids")
    if (not isinstance(group_id, (str, int)) or not isinstance(physical_ids, list)
            or not physical_ids
            or any(not isinstance(card_id, str) or not card_id.strip()
                   for card_id in physical_ids)
            or len(physical_ids) != len(set(physical_ids))):
        return

    matches = []
    for player in result.get("players", []):
        if not player.get("is_hero"):
            continue
        player_ids = [card.get("card_id") for card in player.get("cards", [])]
        if (all(isinstance(card_id, str) and card_id.strip() for card_id in player_ids)
                and player.get("seat_id") == group_id
                and len(player_ids) == len(physical_ids)
                and set(player_ids) == set(physical_ids)):
            matches.append(player)
    if len(matches) != 1:
        return

    player = matches[0]
    if hero_advice.get("ambiguous") is True:
        player["advice"] = "SIN CONSEJO"
        player["advice_reason"] = deepcopy(hero_advice.get("reason") or "HERO_AMBIGUOUS")
        return
    hand_payloads = hero_advice.get("hands")
    if not isinstance(hand_payloads, list):
        return
    attached = []
    for hand_payload in hand_payloads:
        if not isinstance(hand_payload, Mapping):
            continue
        hand = hand_payload.get("hand")
        hand_ids = hand_payload.get("card_ids")
        if (not isinstance(hand_ids, list)
                or any(not isinstance(card_id, str) or not card_id.strip()
                       for card_id in hand_ids)
                or len(hand_ids) != len(set(hand_ids))):
            continue
        if player.get("has_split"):
            if hand is None:
                expected = [card.get("card_id") for card in player.get("cards", [])
                            if type(card.get("hand")) is not int
                            or card.get("hand") not in (0, 1)]
            elif type(hand) is int and hand in (0, 1):
                expected = [card.get("card_id") for card in player.get("cards", [])
                            if card.get("hand") == hand]
            else:
                continue
        else:
            if hand not in (None, 0):
                continue
            expected = [card.get("card_id") for card in player.get("cards", [])]
        if (any(not isinstance(card_id, str) or not card_id.strip()
                for card_id in expected)
                or len(expected) != len(hand_ids) or set(expected) != set(hand_ids)):
            continue
        advice = hand_payload.get("advice")
        valid_action = (isinstance(advice, Mapping)
                        and advice.get("action") in (None, "H", "S", "D", "P", "R",
                                                       "H/S", "D/H", "R/H"))
        safe_advice = (deepcopy(advice) if valid_action
                       and isinstance(advice.get("text"), str)
                       and hand_payload.get("status") == "ready" else None)
        attached.append({
            "hand": hand,
            "hand_id": deepcopy(hand_payload.get("hand_id")),
            "split_index": deepcopy(hand_payload.get("split_index")),
            "card_ids": deepcopy(hand_ids),
            "status": ("ready" if safe_advice is not None else "no_advice"),
            "reason": (None if safe_advice is not None
                       else deepcopy(hand_payload.get("reason"))),
            "advice": safe_advice,
        })
    if len(attached) != len(hand_payloads) or not attached:
        return
    attached_tags = [hand["hand"] for hand in attached]
    all_hand_ids = [card_id for hand in attached for card_id in hand["card_ids"]]
    if len(attached_tags) != len(set(attached_tags)) or len(all_hand_ids) != len(set(all_hand_ids)):
        return
    if set(all_hand_ids) != set(physical_ids):
        return
    if player.get("has_split"):
        expected_tags = {0, 1}
        if any(type(card.get("hand")) is not int or card.get("hand") not in (0, 1)
               for card in player.get("cards", [])):
            expected_tags.add(None)
        if set(attached_tags) != expected_tags:
            return
    elif len(attached) != 1:
        return
    player["hands"] = attached
    display = []
    for item in attached:
        number = (item["hand"] + 1 if type(item["hand"]) is int else None)
        label = (f"Mano {number}: " if number is not None else
                 "Mano sin identificar: " if player.get("has_split") else "")
        if item["advice"]:
            text = item["advice"].get("text") or "SIN CONSEJO"
            optimal = item["advice"].get("optimal")
            if optimal:
                text = f"{text} · {optimal}"
            index_text = item["advice"].get("index_advice")
            if index_text:
                text = f"{text} · {index_text}"
            display.append(label + text)
            result["advice_available"] = True
        else:
            display.append(label + "SIN CONSEJO")
    player["advice"] = "\n".join(display)
    player["advice_reason"] = deepcopy(hero_advice.get("reason"))


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
        "group_count": 0,
        "group_overflow": False,
        "group_warning": None,
    }
    for key in _SOURCE_FIELDS:
        if key in snap:
            result[key] = deepcopy(snap[key])
    result["profile_name"] = deepcopy(result.get("source_profile"))
    result["resolution"] = deepcopy(result.get("source_resolution"))
    identity_context = _identity_context(snap, result, mode)
    result["source_id"] = identity_context["source_id"]
    result["source_ref"] = deepcopy(identity_context["source_ref"])
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
        result["players"] = _build_players(groups, mode, issues, identity_context)
        if mode != INDIVIDUAL:
            result["group_count"] = len(groups)
            result["group_overflow"] = len(groups) > 7
            if result["group_overflow"]:
                result["group_warning"] = ">7 grupos detectados - Revisar calibración"
        dealer_records = _records(snap.get("dealer_cards"), "dealer_cards", issues)
        for card in dealer_records:
            _card_doubts(card, issues)
            if card.get("owner") not in (None, "dealer"):
                issues.append("Carta ajena en la mano del crupier")
        result["dealer"]["cards"] = [
            _normalize_card(card, identity_context=identity_context, group_id="dealer")
            for card in dealer_records]
        status = _reading_status(snap, groups, result["players"], result["dealer"]["cards"], issues)
    result["status"] = status
    result["message"] = _MESSAGES[status]
    _attach_physical_advice(result, snap)
    return result
