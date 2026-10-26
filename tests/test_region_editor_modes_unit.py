"""Pure payloads and editor callbacks with widget doubles; no Tk roots."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import numpy as np
import pytest

from lib.common import constants
from lib.interfaces import region_editor as editor_module
from lib.interfaces.region_editor import RegionEditor, build_layout_payload, EDITOR_ZONE_LABELS
from lib.logic.app_modes import zones_for_mode
from lib.logic.card_layout import CardLayout


RES = (320, 180)


@pytest.fixture(autouse=True)
def isolated_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    assert editor_module.region_profiles.store_path() == tmp_path / "region_profiles.json"


def rect(l, t, r, b):
    return [[l, t], [r, t], [r, b], [l, b]]


def zones(mode):
    values = {"mine": rect(20, 100, 100, 160), "dealer": rect(100, 20, 200, 60),
              "table": rect(5, 85, 315, 175)}
    return [values[key] for key in zones_for_mode(mode)]


def build(mode="individual", scale=1, reference=None):
    scaled = [[[x*scale, y*scale] for x, y in poly] for poly in zones(mode)]
    return build_layout_payload(mode, RES, zones_for_mode(mode), scaled, scale, reference)


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("scale", [1, .5, .75])
def test_payload_exact_regions_scale_and_no_shared_mutations(mode, scale):
    keys = zones_for_mode(mode)
    points = [[[x*scale, y*scale] for x, y in poly] for poly in zones(mode)]
    original = deepcopy(points)
    reference = {"kind": "video_frame", "crop": [1, 2, 3, 4]}
    result = build_layout_payload(mode, RES, keys, points, scale, reference)
    assert result["mode"] == mode and result["resolution"] == list(RES)
    assert set(result) == {"mode", "resolution", "reference_source", *keys}
    assert [result[key] for key in keys] == zones(mode)
    assert points == original
    result["mine"][0][0] = 99
    result["reference_source"]["crop"][0] = 99
    assert points == original and reference["crop"][0] == 1


@pytest.mark.parametrize("mode,expected", [("single_seat", "individual"), ("table", "multiplayer")])
def test_read_aliases_only_emit_official_mode(mode, expected):
    assert build_layout_payload(mode, RES, zones_for_mode(expected), zones(expected), 1)["mode"] == expected


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf"), "1", None, True])
def test_invalid_scale(scale):
    with pytest.raises(ValueError, match="escala"):
        build_layout_payload("individual", RES, ("mine", "dealer"), zones("individual"), scale)


@pytest.mark.parametrize("mode", ["seven_seats", "unknown", None])
def test_invalid_modes(mode):
    with pytest.raises(ValueError):
        build_layout_payload(mode, RES, (), [], 1)


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_missing_zones_wrong_keys_and_invalid_geometry(mode):
    keys = zones_for_mode(mode)
    for index in range(len(keys)):
        points = zones(mode)
        points[index] = []
        with pytest.raises(ValueError):
            build_layout_payload(mode, RES, keys, points, 1)
    with pytest.raises(ValueError):
        build_layout_payload(mode, RES, keys[::-1], zones(mode), 1)
    with pytest.raises(ValueError):
        build_layout_payload(mode, RES, keys, zones(mode)[:-1], 1)
    points = zones(mode)
    points[keys.index("dealer")] = deepcopy(points[keys.index("mine")])
    with pytest.raises(ValueError):
        build_layout_payload(mode, RES, keys, points, 1)
    points = zones(mode)
    points[keys.index("mine")] = [[20, 100], [100, 160], [100, 100], [20, 160]]
    with pytest.raises(ValueError):
        build_layout_payload(mode, RES, keys, points, 1)


@pytest.mark.parametrize("resolution", [None, (320,), (0, 180), (True, 180)])
def test_invalid_resolution(resolution):
    with pytest.raises(ValueError):
        build_layout_payload("individual", resolution, ("mine", "dealer"), zones("individual"), 1)


def test_outside_frame_and_outside_table():
    points = zones("individual")
    points[0][0][0] = -1
    with pytest.raises(ValueError):
        build_layout_payload("individual", RES, ("mine", "dealer"), points, 1)
    points = zones("multiplayer")
    points[1] = rect(0, 100, 100, 160)
    with pytest.raises(ValueError, match="dentro de Mesa"):
        build_layout_payload("multiplayer", RES, zones_for_mode("multiplayer"), points, 1)


@pytest.fixture
def fake_widgets(monkeypatch):
    monkeypatch.setattr(editor_module.tk.Toplevel, "__init__", lambda *a, **kw: None)
    for name in ("transient", "bind", "title", "configure", "grab_set", "_redraw"):
        monkeypatch.setattr(RegionEditor, name, Mock())
    monkeypatch.setattr(RegionEditor, "winfo_screenwidth", lambda self: 240)
    monkeypatch.setattr(RegionEditor, "winfo_screenheight", lambda self: 360)
    for name in ("Canvas", "Frame", "Label", "Checkbutton", "Button", "Entry", "BooleanVar", "StringVar"):
        monkeypatch.setattr(editor_module.tk, name, Mock())
    combo = Mock()
    monkeypatch.setattr(editor_module.ttk, "Combobox", Mock(return_value=combo))
    monkeypatch.setattr(editor_module.ImageTk, "PhotoImage", Mock())
    monkeypatch.setattr(editor_module.region_profiles, "active_name", lambda: "Default")
    return combo


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("existing", [False, True])
def test_constructor_loads_only_mode_zones_and_selector(monkeypatch, fake_widgets, mode, existing):
    data = build(mode)
    layout = CardLayout.from_payload(data, RES) if existing else None
    getter = Mock(return_value=layout)
    monkeypatch.setattr(editor_module.region_profiles, "get_card_layout", getter)
    capture = SimpleNamespace(resolution=RES, grab_bgr=Mock(return_value=np.zeros((180, 320, 3), np.uint8)))
    instance = RegionEditor(SimpleNamespace(), capture, profile="Named", mode=mode)
    keys = zones_for_mode(mode)
    assert instance.zone_keys == keys
    assert instance.mode == mode
    assert instance.scale == .5
    assert instance.zones == ([[[x*.5, y*.5] for x, y in p] for p in zones(mode)] if existing else [[] for _ in keys])
    getter.assert_called_once_with(RES, mode, profile="Named")
    editor_module.ttk.Combobox.assert_called_once_with(
        ANY, state="readonly", width=13,
        values=[EDITOR_ZONE_LABELS[key] for key in keys])
    fake_widgets.current.assert_called_once_with(0)
    assert "polys" not in vars(instance)
    assert "dealer" not in vars(instance)


def test_constructor_rejects_historical_before_capture_or_tk():
    capture = SimpleNamespace(grab_bgr=Mock())
    with pytest.raises(ValueError, match="migración"):
        RegionEditor(None, capture, mode="seven_seats")
    capture.grab_bgr.assert_not_called()


def test_constructor_corrupt_layout_is_not_hidden(monkeypatch):
    monkeypatch.setattr(editor_module.region_profiles, "get_card_layout", Mock(side_effect=ValueError("corrupt layout")))
    capture = SimpleNamespace(resolution=RES, grab_bgr=Mock())
    with pytest.raises(ValueError, match="corrupt layout"):
        RegionEditor(None, capture)
    capture.grab_bgr.assert_not_called()


def fake_editor(mode="individual"):
    obj = SimpleNamespace(mode=mode, zone_keys=zones_for_mode(mode), zones=zones(mode),
                          zone_combo=SimpleNamespace(current=lambda: 0), draw_mode=SimpleNamespace(get=lambda: True),
                          _draw_start=(10, 10), _drag=None, _redraw=Mock(),
                          _photo=SimpleNamespace(width=lambda: 320, height=lambda: 180))
    obj._point = lambda event: RegionEditor._point(obj, event)
    obj._shapes = lambda: RegionEditor._shapes(obj)
    return obj


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_shapes_rectangle_drag_and_vertex_edit_are_local(mode):
    obj = fake_editor(mode)
    original = deepcopy(obj.zones)
    shapes = list(RegionEditor._shapes(obj))
    assert [s[3] for s in shapes] == [EDITOR_ZONE_LABELS[k] for k in obj.zone_keys]
    RegionEditor._move(obj, SimpleNamespace(x=90, y=80))
    assert obj.zones[0] == rect(10, 10, 90, 80)
    assert obj.zones[1:] == original[1:]
    obj.draw_mode.get = lambda: False
    RegionEditor._press(obj, SimpleNamespace(x=10, y=10))
    RegionEditor._move(obj, SimpleNamespace(x=-10, y=300))
    assert obj.zones[0][0] == [0, 180]
    obj.zones[0] = rect(10, 10, 90, 80)
    RegionEditor._edit_vertex(obj, SimpleNamespace(x=50, y=10))
    assert obj.zones[0] == [[10, 10], [50, 10], [90, 10], [90, 80], [10, 80]]
    RegionEditor._edit_vertex(obj, SimpleNamespace(x=50, y=10))
    assert obj.zones[0] == rect(10, 10, 90, 80)
    RegionEditor._edit_vertex(obj, SimpleNamespace(x=10, y=10))
    assert len(obj.zones[0]) == 3
    before = deepcopy(obj.zones)
    RegionEditor._edit_vertex(obj, SimpleNamespace(x=90, y=10))
    assert obj.zones == before
    assert obj.zones[1:] == original[1:]


@pytest.mark.parametrize("index", [-1, 2, 99])
def test_invalid_selector_is_ignored(index):
    obj = fake_editor()
    obj.zone_combo.current = lambda: index
    before = deepcopy(obj.zones)
    RegionEditor._move(obj, SimpleNamespace(x=90, y=80))
    RegionEditor._edit_vertex(obj, SimpleNamespace(x=20, y=100))
    assert obj.zones == before


def test_empty_shapes_do_not_draw_fake_polygons():
    obj = fake_editor()
    obj.zones = [[], []]
    obj.canvas = Mock()
    RegionEditor._redraw(obj)
    obj.canvas.create_polygon.assert_not_called()


@pytest.mark.parametrize("failure", [None, "blank", "write", "read", "missing", "mismatch", "geometry"])
@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
def test_save_verifies_before_callback_and_close(monkeypatch, failure, mode):
    obj = fake_editor(mode)
    context = {"kind": "video_frame", "frame_index": 8}
    obj.capture = SimpleNamespace(calibration_context=context)
    obj.resolution, obj.scale = RES, 1
    obj.profile_var = SimpleNamespace(get=lambda: "  " if failure == "blank" else " Named ")
    obj.on_save, obj.destroy, obj._log_save_error, obj._log_saved = Mock(), Mock(), Mock(), Mock()
    if failure == "geometry":
        obj.zones[0] = []
    original = deepcopy(obj.zones)
    calls = []
    saved_calls = []

    def save(layout, profile=None, source_context=None, *, overwrite=False):
        calls.append("save")
        saved_calls.append({"layout": layout, "profile": profile,
                            "source_context": source_context, "overwrite": overwrite})
        assert profile == "Named"
        assert source_context is None
        assert overwrite is False  # These cases create a new profile, not replace one.
        assert layout.to_payload() == build(mode, reference=context)
        assert layout.to_payload()["reference_source"] == context
        if failure == "write":
            raise OSError("write failed")

    def load(resolution, requested, profile):
        calls.append("read")
        obj.destroy.assert_not_called()
        obj.on_save.assert_not_called()
        assert (resolution, requested, profile) == (RES, mode, "Named")
        if failure == "read":
            raise OSError("read failed")
        if failure == "missing":
            return None
        data = build(mode, reference=context)
        if failure == "mismatch":
            data["mine"][0][0] = 21
        return CardLayout.from_payload(data, RES)

    setter, getter = Mock(side_effect=save), Mock(side_effect=load)
    monkeypatch.setattr(editor_module.region_profiles, "set_card_layout", setter)
    monkeypatch.setattr(editor_module.region_profiles, "get_card_layout", getter)
    profile_exists = Mock(return_value=False)
    target_exists = Mock(return_value=False)
    monkeypatch.setattr(editor_module.region_profiles, "profile_exists", profile_exists)
    monkeypatch.setattr(editor_module.region_profiles, "layout_target_exists", target_exists)
    dialog = Mock(return_value=None)
    monkeypatch.setattr(editor_module.messagebox, "askyesnocancel", dialog)
    error = Mock()
    monkeypatch.setattr(editor_module.messagebox, "showerror", error)
    RegionEditor._save_card_layout(obj)
    assert obj.zones == original
    dialog.assert_not_called()
    if failure in ("blank", "geometry"):
        assert calls == [] and saved_calls == []
        profile_exists.assert_not_called()
        target_exists.assert_not_called()
        setter.assert_not_called()
        getter.assert_not_called()
    else:
        assert len(saved_calls) == 1 and saved_calls[0]["overwrite"] is False
        target_exists.assert_called_once_with(RES, mode, "Named")
        profile_exists.assert_called_once_with("Named")
        setter.assert_called_once()
        if failure == "write":
            assert calls == ["save"]
            getter.assert_not_called()
        else:
            assert calls == ["save", "read"]
            getter.assert_called_once_with(RES, mode, profile="Named")
    if failure:
        error.assert_called_once()
        obj.destroy.assert_not_called()
        obj.on_save.assert_not_called()
        obj._log_saved.assert_not_called()
    else:
        assert calls == ["save", "read"]
        obj.on_save.assert_called_once_with()
        obj.destroy.assert_called_once_with()
        obj._log_saved.assert_called_once()
        error.assert_not_called()


@pytest.mark.parametrize("mode", ["individual", "multiplayer"])
@pytest.mark.parametrize("target", ["new", "existing_profile", "occupied"])
def test_save_intent_matches_explicit_profile_decision(monkeypatch, mode, target):
    obj = fake_editor(mode)
    obj.resolution, obj.scale = RES, 1
    obj.profile_var = SimpleNamespace(get=lambda: " Named ")
    obj.profile_entry = Mock()
    obj.capture = SimpleNamespace(calibration_context=None)
    obj.on_save, obj.destroy = Mock(), Mock()
    obj._log_save_error, obj._log_saved = Mock(), Mock()
    points = deepcopy(obj.zones)
    occupied = target == "occupied"
    exists = target != "new"
    target_query = Mock(return_value=occupied)
    profile_query = Mock(return_value=exists)
    monkeypatch.setattr(editor_module.region_profiles, "layout_target_exists", target_query)
    monkeypatch.setattr(editor_module.region_profiles, "profile_exists", profile_query)
    dialog = Mock(return_value=True)
    monkeypatch.setattr(editor_module.messagebox, "askyesnocancel", dialog)
    error = Mock()
    monkeypatch.setattr(editor_module.messagebox, "showerror", error)
    saved_calls = []

    def save(layout, profile=None, source_context=None, *, overwrite=False):
        saved_calls.append({"layout": layout, "profile": profile,
                            "source_context": source_context, "overwrite": overwrite})
        assert profile == "Named" and source_context is None
        assert overwrite is occupied
        assert layout.to_payload() == build(mode)
        assert dialog.call_count == int(exists)  # Consent precedes persistence.

    def load(resolution, requested, profile=None):
        assert len(saved_calls) == 1
        assert (resolution, requested, profile) == (RES, mode, "Named")
        obj.on_save.assert_not_called()
        obj.destroy.assert_not_called()
        return saved_calls[0]["layout"]

    setter, getter = Mock(side_effect=save), Mock(side_effect=load)
    monkeypatch.setattr(editor_module.region_profiles, "set_card_layout", setter)
    monkeypatch.setattr(editor_module.region_profiles, "get_card_layout", getter)
    RegionEditor._save_card_layout(obj)
    assert len(saved_calls) == 1
    assert saved_calls[0]["overwrite"] is occupied
    assert obj.zones == points
    target_query.assert_called_once_with(RES, mode, "Named")
    assert profile_query.call_count == (0 if occupied else 1)
    assert dialog.call_count == int(exists)
    if exists:
        assert dialog.call_args.args[0] == (
            "La calibración ya existe" if occupied else "El perfil ya existe")
    setter.assert_called_once()
    getter.assert_called_once()
    obj.on_save.assert_called_once_with()
    obj.destroy.assert_called_once_with()
    obj._log_saved.assert_called_once()
    obj._log_save_error.assert_not_called()
    obj.profile_entry.focus_set.assert_not_called()
    error.assert_not_called()
