"""Calibration UI contracts with fakes only: no Tk roots, capture or models."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from lib.interfaces.modern_gui import ModernBlackjackGUI as GUI
from lib.interfaces.region_editor import RegionEditor


def fake_gui():
    gui = SimpleNamespace(
        controller=SimpleNamespace(running=False, set_monitor=Mock(side_effect=ValueError('Missing seven-seat layout'))),
        monitor_combo=SimpleNamespace(current=lambda: 0),
        source_kind=SimpleNamespace(get=lambda: 'Monitor'),
        _monitors=[SimpleNamespace(width=1600, height=1028)],
        _source_configured=True, _preview_source=None, _region_editor=None,
        ui_actions=Mock(current=None, current_id=None), log_manager=Mock(),
        set_status=Mock(), _calibration_capture=Mock(),
    )
    for name in ('start_btn', 'calibrate_btn', 'regions_btn', 'ocr_btn',
                 'controls_btn', 'anchors_btn', 'monitor_status'):
        setattr(gui, name, Mock())
    return gui


class CalibrationUIUnitTests(unittest.TestCase):
    def test_missing_layout_keeps_calibration_available_and_start_disabled(self):
        gui = fake_gui()
        self.assertFalse(GUI._confirm_monitor(gui))
        self.assertFalse(gui._source_configured)
        self.assertIs(gui.monitor, gui._monitors[0])
        gui.start_btn.config.assert_called_with(state='disabled')
        gui.calibrate_btn.config.assert_called_with(state='normal')
        text = gui.monitor_status.config.call_args.kwargs['text']
        self.assertIn('1600', text)
        self.assertIn('Calibrate Regions', text)

    def test_invalid_selection_revokes_previous_source(self):
        gui = fake_gui()
        gui.monitor_combo.current = lambda: -1
        self.assertFalse(GUI._confirm_monitor(gui))
        self.assertFalse(gui._source_configured)
        self.assertIsNone(gui.monitor)
        gui.start_btn.config.assert_called_with(state='disabled')

    def test_existing_editor_reused_without_capture(self):
        gui = fake_gui()
        gui.monitor = gui._monitors[0]
        gui._region_editor = Mock()
        gui._region_editor.winfo_exists.return_value = True
        GUI._calibrate_regions(gui)
        gui._region_editor.show_editor.assert_called_once()
        gui._calibration_capture.assert_not_called()

    def test_capture_failure_visible_and_retry_remains_available(self):
        gui = fake_gui()
        gui.monitor = gui._monitors[0]
        gui._calibration_capture.side_effect = RuntimeError('capture unavailable')
        with patch('lib.interfaces.modern_gui.messagebox.showerror') as error:
            GUI._calibrate_regions(gui)
        self.assertIn('capture unavailable', gui.set_status.call_args.args[0])
        self.assertTrue(gui.set_status.call_args.kwargs['error'])
        error.assert_called_once()
        self.assertIsNone(gui._region_editor)
        self.assertIn('CALIBRATION_OPEN_FAILED',
                      [call.args[0] for call in gui.ui_actions.record.call_args_list])

    def test_pending_calibration_cannot_start_detection(self):
        gui = fake_gui()
        gui.monitor = gui._monitors[0]
        gui._source_configured = False
        gui.controller.start = Mock()
        GUI._toggle_detection(gui)
        gui.controller.start.assert_not_called()
        gui.ui_actions.blocked.assert_called_once()

    def test_mapped_feedback_only_for_editor_and_once(self):
        parent = SimpleNamespace(ui_actions=Mock(), set_status=Mock())
        editor = SimpleNamespace(_shown_logged=False, _editor_parent=parent,
                                 resolution=(1600, 1028), mode='seven_seats')
        RegionEditor._on_editor_mapped(editor, SimpleNamespace(widget=object()))
        parent.ui_actions.record.assert_not_called()
        RegionEditor._on_editor_mapped(editor, SimpleNamespace(widget=editor))
        RegionEditor._on_editor_mapped(editor, SimpleNamespace(widget=editor))
        parent.ui_actions.record.assert_called_once()
        self.assertEqual(parent.ui_actions.record.call_args.args[0], 'CALIBRATION_SHOWN')

    def test_show_editor_centers_restores_and_uses_no_topmost(self):
        editor = Mock()
        editor._editor_parent = SimpleNamespace(winfo_rootx=lambda: 100,
            winfo_rooty=lambda: 80, winfo_width=lambda: 1000, winfo_height=lambda: 700)
        editor.winfo_reqwidth.return_value = 800
        editor.winfo_reqheight.return_value = 500
        editor.winfo_screenwidth.return_value = 1920
        editor.winfo_screenheight.return_value = 1080
        RegionEditor.show_editor(editor)
        editor.geometry.assert_called_once_with('+200+180')
        editor.deiconify.assert_called_once()
        editor.lift.assert_called_once()
        editor.attributes.assert_not_called()
    def test_calibration_capture_uses_selected_ready_source_and_guard(self):
        gui = fake_gui()
        ready = SimpleNamespace(width=1600, height=1028)
        gui.monitor = SimpleNamespace(ready_source=Mock(return_value=ready))
        gui._capture_guard = object()
        previous = object()
        gui.controller.engine = SimpleNamespace(capture=previous)
        capture = Mock()
        with patch('lib.logic.monitor_utils.ScreenCapture', return_value=capture):
            result = GUI._calibration_capture(gui)
        self.assertIs(result, capture)
        self.assertIsNot(result, previous)
        capture.set_monitor.assert_called_once_with(ready)
        self.assertIs(capture.guard, gui._capture_guard)

    def test_open_constructs_editor_from_selected_capture_and_closes_local(self):
        gui = fake_gui()
        gui.monitor = gui._monitors[0]
        capture = Mock(resolution=(1600, 1028))
        gui._calibration_capture.return_value = capture
        editor = Mock(_shown_logged=False)
        with patch('lib.interfaces.region_editor.RegionEditor', return_value=editor) as ctor, \
             patch('lib.logic.region_profiles.layout_mode', return_value='seven_seats'), \
             patch('lib.logic.region_profiles.active_name', return_value='Table'):
            GUI._calibrate_regions(gui)
        self.assertIs(ctor.call_args.args[1], capture)
        self.assertEqual(ctor.call_args.kwargs['mode'], 'seven_seats')
        self.assertIs(gui._region_editor, editor)
        editor.show_editor.assert_called_once()
        capture.close_local.assert_called_once()
        self.assertNotIn('CALIBRATION_SHOWN',
                         [call.args[0] for call in gui.ui_actions.record.call_args_list])
