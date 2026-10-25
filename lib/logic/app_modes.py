"""Public application modes and their required calibration zones."""

INDIVIDUAL = "individual"
MULTIPLAYER = "multiplayer"

APP_MODES = (INDIVIDUAL, MULTIPLAYER)

APP_MODE_LABELS = {
    INDIVIDUAL: "Individual",
    MULTIPLAYER: "Multijugador",
}


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
