"""Named region-calibration profiles — one per casino table.

A profile bundles BOTH calibration kinds per capture resolution: the
seat/dealer regions (Calibrate Regions editor) and the OCR rects
(balance/bet/result) — a different table moves both, so switching tables
swaps everything at once. monitor_utils and ocr route their load/save/
delete through the ACTIVE profile, so the engine and the editors need no
knowledge of profiles.

Store: output/region_profiles.json
{
  "active": "Default",
  "profiles": {
    "Default": {
      "saved": "2026-06-11T16:40:00",
      "regions": {"2560x1440": {"players": [...], "dealer": [l,t,r,b]}},
      "ocr":     {"2560x1440": {"balance": [l,t,r,b], ...}}
    }
  }
}

Legacy per-resolution files (regions_{w}x{h}.json, ocr_regions_{w}x{h}.json)
are folded into "Default" the first time the store is touched and renamed
to *.migrated so they are never re-imported. A plain read on a fresh
install creates no files. Saving into a profile stamps `saved` (shown in
the GUI's profile list) and makes it the active one.
"""

import json
import re
import threading
from datetime import datetime

from ..common import constants
from .app_modes import INDIVIDUAL, MULTIPLAYER, APP_MODES, normalize_mode
from .card_layout import SEVEN_SEATS, CardLayout

DEFAULT_NAME = "Default"


class ProfileConflictError(ValueError):
    """A stored calibration would be overwritten."""

# Reads come from the engine worker (set_monitor) while editors write from
# the Tk thread; the RLock serializes the read-modify-write cycles.
_LOCK = threading.RLock()
_LEGACY = (("regions", re.compile(r"^regions_(\d+)x(\d+)\.json$")),
           ("ocr", re.compile(r"^ocr_regions_(\d+)x(\d+)\.json$")))


def store_path():
    return constants.OUTPUT_DIR / "region_profiles.json"


def _res_key(resolution):
    w, h = resolution
    return f"{w}x{h}"


def _new_profile():
    return {"saved": None, "regions": {}, "ocr": {}, "controls": {},
            "anchors": {}, "layouts": {}, "source_contexts": {}}


def _empty():
    return {"active": DEFAULT_NAME, "profiles": {DEFAULT_NAME: _new_profile()}}


def _load(*, read_only=False):
    """The whole store; folds legacy per-resolution files in on first touch."""
    with _LOCK:
        path = store_path()
        if not path.exists():
            return _empty() if read_only else _migrate_legacy()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profiles = data.get("profiles")
            if not isinstance(profiles, dict) or not profiles:
                raise ValueError("no profiles")
            for prof in profiles.values():
                prof.setdefault("saved", None)
                for kind in ("regions", "ocr", "controls", "anchors", "layouts", "source_contexts"):
                    # setdefault alone lets an existing null/list through,
                    # which would crash every _get/_set downstream.
                    if not isinstance(prof.get(kind), dict):
                        prof[kind] = {}
            if data.get("active") not in profiles:
                data["active"] = sorted(profiles)[0]
            return data
        except OSError as e:
            # Transient read failure (AV/sync lock) on a possibly VALID
            # file: serve defaults for this read but block writes — a save
            # now would clobber recoverable data.
            print(f"Region-profile store unreadable ({e}); "
                  "read-only defaults until it recovers.")
            data = _empty()
            data["_locked"] = True
            return data
        except (ValueError, TypeError, AttributeError) as e:
            if read_only:
                raise ValueError(f"Region-profile store corrupt: {e}") from e
            # Parse/shape corruption: quarantine the bytes first — the next
            # save would otherwise atomically replace every table's
            # calibration with an empty store.
            print(f"Region-profile store corrupt ({e}); "
                  f"moved to {path.name}.corrupt, starting fresh.")
            try:
                path.replace(path.with_name(path.name + ".corrupt"))
            except OSError:
                pass
            return _empty()


def _migrate_legacy():
    """Pre-profile files become the 'Default' profile, stamped with their
    newest mtime. The store is only written when something was actually
    migrated — a read on a fresh install must not create files."""
    data = _empty()
    default = data["profiles"][DEFAULT_NAME]
    migrated, newest = [], None
    try:
        entries = list(constants.OUTPUT_DIR.iterdir())
    except OSError:
        return data
    for entry in entries:
        for kind, pattern in _LEGACY:
            m = pattern.match(entry.name)
            if not m:
                continue
            try:
                payload = json.loads(entry.read_text(encoding="utf-8"))
                mtime = entry.stat().st_mtime
            except (OSError, ValueError):
                break  # unreadable legacy file: leave it alone
            default[kind][f"{m.group(1)}x{m.group(2)}"] = payload
            newest = mtime if newest is None else max(newest, mtime)
            migrated.append(entry)
            break
    if migrated:
        default["saved"] = datetime.fromtimestamp(newest).isoformat(
            timespec="seconds")
        try:
            _write(data)
        except OSError as e:
            # Disk not writable right now: serve the merged data in memory
            # and leave the legacy files untouched, so the next launch
            # retries the migration cleanly.
            print(f"Region-profile migration deferred ({e}).")
            return data
        for entry in migrated:
            try:
                entry.rename(entry.with_name(entry.name + ".migrated"))
            except OSError:
                pass  # payload already lives in the store; the lingering
                # legacy file is simply never re-imported
    return data


def _write(data):
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------- public

def list_profiles():
    """[{'name', 'saved'}] — Default first, the rest alphabetical."""
    data = _load()
    items = [{"name": name, "saved": prof.get("saved"),
              "card_resolutions": sorted(key for key, modes in prof.get("layouts", {}).items()
                                         if _valid_card_resolution(key, modes)),
              "ocr_resolutions": sorted(key for key, value in prof.get("ocr", {}).items() if value)}
             for name, prof in data["profiles"].items()]
    items.sort(key=lambda p: (p["name"] != DEFAULT_NAME, p["name"].lower()))
    return items


def calibration_resolution_summary(profile_item):
    """Short, real saved resolutions for card layouts and separate OCR data."""
    cards = tuple(profile_item.get("card_resolutions", ()))
    ocr = tuple(profile_item.get("ocr_resolutions", ()))
    return cards, ocr


def active_name() -> str:
    return _load()["active"]


def set_active(name) -> bool:
    """Switch the active profile; False when the name doesn't exist."""
    with _LOCK:
        data = _load()
        if name not in data["profiles"]:
            return False
        if data["active"] != name:
            data["active"] = name
            _write(data)
        return True


def _get(kind, resolution, profile=None):
    """The active profile's payload for this kind/resolution — or a NAMED
    profile's when `profile` is given (None for unknown names)."""
    data = _load()
    prof = data["profiles"].get(profile) if profile \
        else data["profiles"][data["active"]]
    return prof[kind].get(_res_key(resolution)) if prof else None


def _set(kind, resolution, payload, profile, layout_mode=None):
    with _LOCK:
        data = _load()
        if data.pop("_locked", False):
            print("Region-profile store is locked — calibration not saved; "
                  "try again in a moment.")
            return
        name = (profile or data["active"]).strip() or data["active"]
        prof = data["profiles"].setdefault(name, _new_profile())
        prof[kind][_res_key(resolution)] = payload
        if layout_mode is not None:
            prof["layouts"].setdefault(_res_key(resolution), {})["selected_mode"] = layout_mode
        prof["saved"] = datetime.now().isoformat(timespec="seconds")
        data["active"] = name  # saving into a profile selects it
        _write(data)


def _delete(kind, resolution):
    with _LOCK:
        data = _load()
        if data.pop("_locked", False):
            return
        prof = data["profiles"][data["active"]]
        if prof[kind].pop(_res_key(resolution), None) is not None:
            _write(data)


def get_regions(resolution, profile=None):
    return _get("regions", resolution, profile=profile)


def set_regions(resolution, payload, profile=None):
    _set("regions", resolution, payload, profile, layout_mode=SEVEN_SEATS)


def set_source_context(resolution, context, profile=None):
    """Remember the non-sensitive capture source reference for calibration."""
    clean = clean_source_context(context)
    with _LOCK:
        data = _load()
        if data.pop("_locked", False):
            return
        name = profile or data["active"]
        prof = data["profiles"].setdefault(name, _new_profile())
        prof["source_contexts"][_res_key(resolution)] = clean
        prof["saved"] = datetime.now().isoformat(timespec="seconds")
        _write(data)


def get_source_context(resolution, profile=None):
    return _get("source_contexts", resolution, profile=profile)


def has_calibration(resolution, profile=None):
    data = _load()
    prof = data["profiles"].get(profile) if profile else data["profiles"][data["active"]]
    if not prof:
        return False
    key = _res_key(resolution)
    return any(bool(prof.get(kind, {}).get(key))
               for kind in ("regions", "ocr", "controls", "anchors", "layouts"))


def source_contexts(profile=None):
    data = _load()
    prof = data["profiles"].get(profile) if profile else data["profiles"][data["active"]]
    return dict(prof.get("source_contexts", {})) if prof else {}


def clean_source_context(context):
    if not isinstance(context, dict):
        return {}
    return {key: context[key] for key in ("kind", "source_id", "width", "height")
            if key in context and isinstance(context[key], (str, int, float, bool))}


def clean_calibration_reference(context):
    """Keep the non-pixel video-frame provenance used by saved layouts.

    Window/monitor hints remain deliberately narrow. Video provenance is a
    separate contract: preserve its local path, frame, crop, and timestamps,
    while excluding arbitrary values such as image arrays or credentials.
    """
    if not isinstance(context, dict):
        return {}
    if context.get("kind") != "video_frame":
        return clean_source_context(context)
    allowed = ("kind", "path", "native_resolution", "resolution", "crop",
               "frame_index", "timestamp_seconds")

    def safe(value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return True
        return (isinstance(value, (list, tuple)) and
                all(isinstance(item, (str, int, float, bool)) or item is None
                    for item in value))

    return {key: list(value) if isinstance(value, tuple) else value
            for key, value in context.items() if key in allowed and safe(value)}


def delete_regions(resolution):
    _delete("regions", resolution)


def layout_mode(resolution, profile=None):
    """UI selection only; the default does not imply a usable calibration."""
    saved = _layout_modes(resolution, profile)
    mode = saved.get("selected_mode", INDIVIDUAL)
    return INDIVIDUAL if mode == SEVEN_SEATS else normalize_mode(mode)


def _layout_modes(resolution, profile):
    return _stored_layout_modes(_load(), resolution, profile)


def _stored_layout_modes(data, resolution, profile):
    if data.get("_locked"):
        raise OSError("Region-profile store unreadable; layout access blocked.")
    prof = data["profiles"].get(profile) if profile else data["profiles"][data["active"]]
    saved = prof["layouts"].get(_res_key(resolution)) if prof else None
    if saved is None:
        return {}
    if not isinstance(saved, dict):
        raise ValueError("Las zonas guardadas de esta resolución tienen un formato inválido.")
    return saved


def get_card_layout(resolution, mode, profile=None):
    """Validated new-mode layout, or None. No defaults, scaling or mode fallback."""
    mode = normalize_mode(mode)
    saved = _layout_modes(resolution, profile)
    return _card_layout_from_modes(saved, resolution, mode)


def _card_layout_from_modes(saved, resolution, mode):
    legacy = "single_seat" if mode == INDIVIDUAL else "table"
    key = mode if mode in saved else legacy
    if key not in saved:
        return None
    layout = CardLayout.from_payload(saved[key], resolution)
    if layout.mode != mode:
        raise ValueError("El modo del layout no coincide con su clave guardada.")
    return layout


def has_card_layout(resolution, mode, profile=None) -> bool:
    """False for missing/corrupt geometry; explicit loads raise. I/O errors propagate."""
    try:
        return get_card_layout(resolution, mode, profile=profile) is not None
    except ValueError:
        return False


def _valid_card_resolution(key, modes):
    if not isinstance(modes, dict):
        return False
    try:
        resolution = tuple(int(v) for v in key.split("x"))
        if len(resolution) != 2 or _res_key(resolution) != key:
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    for mode in APP_MODES:
        try:
            if _card_layout_from_modes(modes, resolution, mode) is not None:
                return True
        except ValueError:
            continue
    return False


def profile_exists(name: str) -> bool:
    """Check stored names, stripping outer spaces only. Empty/non-string is False."""
    if not isinstance(name, str) or not name.strip():
        return False
    with _LOCK:
        if not store_path().exists():
            return False  # Do not create/migrate storage for an existence query.
        data = _load(read_only=True)
        if data.get("_locked"):
            raise OSError("Region-profile store unreadable; profile access blocked.")
        return name.strip() in data["profiles"]


def _layout_target_occupied(saved, mode):
    return mode in saved or ("single_seat" if mode == INDIVIDUAL else "table") in saved


def layout_target_exists(resolution, mode, profile) -> bool:
    """Stored presence, including invalid payloads and compatible aliases, not validity."""
    mode = normalize_mode(mode)
    if not isinstance(profile, str) or not profile.strip():
        return False
    with _LOCK:
        if not store_path().exists():
            return False
        name = profile.strip() if isinstance(profile, str) else profile
        return _layout_target_occupied(_stored_layout_modes(_load(read_only=True), resolution, name), mode)


def set_card_layout(layout, profile=None, source_context=None, *, overwrite=False):
    payload = layout.to_payload()
    if source_context is not None:
        payload["reference_source"] = clean_calibration_reference(source_context)
    # Revalidate at the write boundary, including objects built without from_payload.
    checked = CardLayout.from_payload(payload, layout.resolution)
    with _LOCK:
        data = _load()
        name = (profile or data["active"]).strip() or data["active"]
        saved = dict(_stored_layout_modes(data, checked.resolution, name))
        if _layout_target_occupied(saved, checked.mode) and overwrite is not True:
            raise ProfileConflictError(
                f'El perfil "{name}" ya tiene una calibración {checked.mode} '
                f'para {_res_key(checked.resolution)}. Se requiere autorización para reemplazarla.')
        saved[checked.mode] = checked.to_payload()
        saved.pop("single_seat" if checked.mode == INDIVIDUAL else "table", None)
        saved["selected_mode"] = checked.mode
        prof = data["profiles"].setdefault(name, _new_profile())
        prof["layouts"][_res_key(checked.resolution)] = saved
        prof["saved"] = datetime.now().isoformat(timespec="seconds")
        data["active"] = name
        _write(data)


def get_ocr(resolution):
    return _get("ocr", resolution)


def set_ocr(resolution, payload, profile=None):
    _set("ocr", resolution, payload, profile)


def delete_ocr(resolution):
    _delete("ocr", resolution)


# Control-template calibration (V4 Feature 1): per-resolution rects + the
# template filenames for the action buttons / bet spot, captured with the
# Capture Controls editor. Payload shape per resolution:
#   {"hit": {"rect": [l,t,r,b], "template": "hit.png"}, ...}
# Template images live under assets/controls/<profile-slug>/<WxH>/.

def get_controls(resolution, profile=None):
    return _get("controls", resolution, profile=profile)


def set_controls(resolution, payload, profile=None):
    _set("controls", resolution, payload, profile)


def delete_controls(resolution):
    _delete("controls", resolution)


# UI anchors (V3 E3): per-resolution template crops of stable casino-UI
# elements, used to solve a scale+offset transform that maps the one
# calibrated region set to whatever the screen actually shows. Payload
# shape mirrors controls: {"anchor_a": {"rect": [...], "template":
# "anchor-anchor_a.png"}, ...}; PNGs beside the control templates.

def get_anchors(resolution, profile=None):
    return _get("anchors", resolution, profile=profile)


def set_anchors(resolution, payload, profile=None):
    _set("anchors", resolution, payload, profile)


def delete_anchors(resolution):
    _delete("anchors", resolution)


def anchor_resolutions(profile=None) -> list:
    """Resolution keys ('WxH') holding anchors — for picking the calibrated
    set to map when the live resolution has none of its own."""
    data = _load()
    prof = data["profiles"].get(profile) if profile \
        else data["profiles"][data["active"]]
    if not prof:
        return []
    return sorted(key for key, payload in prof["anchors"].items() if payload)
