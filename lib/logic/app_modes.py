"""Public application modes and their required calibration zones."""

INDIVIDUAL = "individual"
MULTIPLAYER = "multiplayer"

APP_MODES = (INDIVIDUAL, MULTIPLAYER)

APP_MODE_LABELS = {
    INDIVIDUAL: "Individual",
    MULTIPLAYER: "Multijugador",
}


SURFACE_DIGITAL = "digital"
SURFACE_PHYSICAL = "physical"

SURFACE_ROUTING = {
    INDIVIDUAL: {
        "dealer": SURFACE_DIGITAL,
        "mine": SURFACE_DIGITAL,
    },
    MULTIPLAYER: {
        "dealer": SURFACE_DIGITAL,
        "mine": SURFACE_PHYSICAL,
        "table": SURFACE_PHYSICAL,
    },
}


def surface_type_for_zone(mode: str, zone: str) -> str:
    """Return 'digital' or 'physical' surface type for a given mode and zone."""
    try:
        norm = normalize_mode(mode)
    except Exception:
        norm = INDIVIDUAL if mode == "individual" else MULTIPLAYER
    if zone == "dealer":
        return SURFACE_DIGITAL
    mapping = SURFACE_ROUTING.get(norm, {})
    return mapping.get(zone, SURFACE_PHYSICAL)


def is_digital_zone(mode: str, zone: str) -> bool:
    """Check if a given zone in the specified mode represents 2D digital cards."""
    return surface_type_for_zone(mode, zone) == SURFACE_DIGITAL


def zones_for_mode(mode: str) -> tuple[str, ...]:
    if mode == INDIVIDUAL:
        return ("mine", "dealer")

    if mode == MULTIPLAYER:
        return ("table", "mine", "dealer")

    raise ValueError(f"Modo desconocido: {mode}")


def normalize_mode(mode: str) -> str:
    """Normalize supported aliases without migrating historical profiles."""
    if mode == INDIVIDUAL or mode == "single_seat":
        return INDIVIDUAL

    if mode == MULTIPLAYER or mode == "table":
        return MULTIPLAYER

    if mode == "seven_seats":
        raise ValueError(
            "El formato histórico seven_seats requiere migración explícita."
        )

    raise ValueError(f"Modo desconocido: {mode}")
