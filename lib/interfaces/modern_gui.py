"""Main application window.

Architecture: the window never talks to models or the screen directly. A
DetectionController runs the engine on a worker thread; this GUI polls
`engine.get_snapshot()` from a Tk `after()` loop and renders the result.
Every user action calls a thread-safe engine method. No Tkinter object is
ever touched from the worker thread.
"""

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import screeninfo

from ..common import constants
from ..logic.app_modes import INDIVIDUAL, MULTIPLAYER, APP_MODE_LABELS
from ..logic.background import DetectionController
from ..logic.counting import COUNTER_KEYS
from ..logic.phase import PHASE_LABELS
from ..logic.region_preview import build_region_preview
from . import scaling
from .card_picker import CardPicker
from .table_view import TableView
from .tooltip import ToolTip
from .validation import attach_numeric_entry

C = constants.COLORS
MODE_BY_LABEL = {label: mode for mode, label in APP_MODE_LABELS.items()}


def format_model_runtime_hint(metadata):
    """Format only snapshot-reported runtime identities; never inspect files."""
    metadata = metadata if isinstance(metadata, dict) else {}
    loaded = [(kind, item) for kind, item in metadata.items()
              if isinstance(item, dict) and item.get("loaded")]
    if not loaded:
        return "Models: not loaded"
    labels = []
    for kind, item in loaded:
        label = item.get("artifact") or item.get("model") or kind
        parts = [str(label)]
        version = item.get("version")
        quantization = item.get("quantization")
        if version:
            version = str(version)
            parts.append(version if version.casefold().startswith("v") else f"v{version}")
        if quantization and str(quantization).casefold() != "unknown":
            parts.append(str(quantization))
        labels.append(" ".join(parts))
    return "Model: " + " + ".join(dict.fromkeys(labels))


class ModernBlackjackGUI(tk.Tk):
    def __init__(self, log_manager=None):
        super().__init__()
        # Scaling first: every widget below picks up the live FONT_* objects
        # and px() sizes resolved for this monitor's DPI.
        scaling.init(self)
        self.title(constants.TITLE)
        width, height = self._initial_geometry()
        self.geometry(f"{width}x{height}")
        self._minsize_applied = (min(scaling.px(1150), width),
                                 min(scaling.px(760), height))
        self.minsize(*self._minsize_applied)
        self.configure(bg=C["bg_primary"])

        from ..logic.log_manager import LogManager
        from .ui_actions import UIActionLogger
        self.log_manager = log_manager or LogManager()
        self.log_manager.start_session(constants.LOGS_DIR)
        self.ui_actions = UIActionLogger(self, self.log_manager)
        self._detection_action = None
        self._preview_action = None
        self._source_configured = False
        self._region_editor = None
        # add_log (not print) so engine WARNING/ERROR levels survive into the
        # log window instead of being folded into the message text.
        self.controller = DetectionController(
            log=self.log_manager.add_log)
        self.monitor = None
        self._preview_source = None
        self.round_overlay = None
        self.hud = None  # compatibility alias for the same Mini Mesa window
        from .capture_guard import CaptureGuard
        self._capture_guard = CaptureGuard(self, lambda: self.round_overlay)
        self.controller.engine.capture.guard = self._capture_guard
        self._last_seq = -1
        self._preview_busy = False

        # Ghost-mode executor (V4 Feature 2): a second consumer of the same
        # snapshot. step() runs in the render path (pure dict math); clicks
        # only ever fire from the confirm hotkey in assist mode.
        engine = self.controller.engine
        from ..logic.executor import Executor
        self.executor = Executor(
            snapshot_fn=engine.get_snapshot,
            monitor_fn=lambda: self.monitor,
            store=engine.store,
            io_submit=engine.submit_io,
            log=log_manager.add_log if log_manager else print,
            running_fn=lambda: self.controller.running,
            allowed=False)
        self.ghost_marker = None
        self._hotkeys = None

        # Row 0 nav bar, row 1 sidebar + table (the stretchy row), row 2 status.
        self.columnconfigure(0, weight=0, minsize=scaling.px(340))
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_nav_bar()
        self._build_left_panel()
        self._build_table()
        self._build_status_bar()
        scaling.watch(self)
        # Fonts and the table rescale themselves on a DPI change; the fixed
        # px() chrome (sidebar width, nav height, minsize) must follow too.
        scaling.on_change(self._apply_chrome_scale)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(constants.SNAPSHOT_POLL_MS, self._poll_snapshot)
        self.after(300, lambda: self.table.preload_images())
        self.after(150, self._auto_select_monitor)
        self.after(250, self._toggle_round_overlay)
        self.after(600, self._maybe_restore_shoe)
        self.set_status("Ready — confirm a monitor, then press Start Detection.")
        if self.log_manager.persistence_error:
            self.set_status(f"No se están guardando los logs: {self.log_manager.persistence_error}", error=True)
        elif self.ui_actions.install_error:
            self.set_status(f"No se activó el registro de botones: {self.ui_actions.install_error}", error=True)

    def _apply_chrome_scale(self):
        """Re-apply the fixed px() dimensions after a DPI rescale (monitor
        move or a settings override) — fonts and the table already updated."""
        self.columnconfigure(0, minsize=scaling.px(340))
        self._sidebar_canvas.config(width=scaling.px(320))
        self.nav_bar.config(height=scaling.px(42))
        # Minsize follows the CURRENT monitor (not the primary) and is only
        # re-asserted on change — a redundant minsize can emit WM resizes
        # that re-feed the DPI watcher after its guard releases.
        _, _, work_w, work_h = scaling.workarea(self)
        minsize = (min(scaling.px(1150), int(work_w * 0.92)),
                   min(scaling.px(760), int(work_h * 0.92)))
        if minsize != self._minsize_applied:
            self._minsize_applied = minsize
            self.minsize(*minsize)
        ttk.Style(self).configure("Dark.Vertical.TScrollbar",
                                  width=scaling.px(10))

    def _initial_geometry(self):
        """The 1500x950 design size scaled for this monitor's DPI, clamped to
        ~92% of the monitor so the window never opens larger than the screen."""
        try:
            monitors = screeninfo.get_monitors()
            mon = next((m for m in monitors if getattr(m, "is_primary", False)),
                       monitors[0])
            max_w, max_h = int(mon.width * 0.92), int(mon.height * 0.92)
        except Exception:
            max_w, max_h = scaling.px(1500), scaling.px(950)
        return min(scaling.px(1500), max_w), min(scaling.px(950), max_h)

    # -------------------------------------------------------------- nav bar

    def _build_nav_bar(self):
        """Top bar: app name + version left, window-opening buttons right.
        Trims the sidebar to table actions only, so it rarely needs to scroll."""
        nav = tk.Frame(self, bg=C["bg_secondary"], height=scaling.px(42))
        nav.grid(row=0, column=0, columnspan=2, sticky="ew")
        nav.pack_propagate(False)
        # 1px bottom border (highlightthickness would also frame the sides).
        tk.Frame(nav, bg=C["border"], height=1).pack(side=tk.BOTTOM, fill=tk.X)
        self.nav_bar = nav

        tk.Label(nav, text="Blackjack AI", font=constants.FONT_SECTION,
                 bg=C["bg_secondary"], fg=C["text_primary"]
                 ).pack(side=tk.LEFT, padx=(14, 4))
        tk.Label(nav, text=f"v{constants.VERSION}", font=constants.FONT_SMALL,
                 bg=C["bg_secondary"], fg=C["text_secondary"]).pack(side=tk.LEFT)

        # Reversed so side=RIGHT packing shows them left-to-right as listed.
        for text, command, tooltip in reversed([
            ("🗒  Logs", self._open_logs, "Open the live log window"),
            ("📊  Stats", self._open_stats,
             "Round history, count distribution, CSV export"),
            ("🩺  Leaks", self._open_leaks,
             "What your mistakes cost — ranked, EV-priced, drillable"),
            ("🛡  Bankroll", self._open_bankroll,
             "Risk of ruin, Kelly risk table, Monte Carlo simulation"),
            ("🎓  Trainer", self._open_trainer,
             "Deck countdown, deviation flashcards, replay drills"),
            ("🎯  Mini Mesa", self._toggle_hud,
             "Mostrar u ocultar el único cartel sobre la mesa"),
            ("⚙  Settings", self._open_settings,
             "Table rules, deck count, side-bet paytables"),
        ]):
            btn = self._nav_button(nav, text, command, tooltip)
            if command == self._toggle_hud:  # bound methods: == not `is`
                self.hud_nav_btn = btn

    def _nav_button(self, parent, text, command, tooltip):
        btn = self._button(parent, text, command, C["bg_secondary"],
                           fg=C["text_primary"], hover=C["bg_primary"],
                           tooltip=tooltip)
        btn.config(padx=scaling.px(10), pady=scaling.px(4))
        btn.pack(side=tk.RIGHT, padx=2)
        return btn

    # ------------------------------------------------------------ left panel

    def _build_left_panel(self):
        left = tk.Frame(self, bg=C["bg_secondary"],
                        highlightbackground=C["border"], highlightthickness=1)
        left.grid(row=1, column=0, sticky="nsew")

        canvas = tk.Canvas(left, bg=C["bg_secondary"], highlightthickness=0,
                           width=scaling.px(320))
        self._sidebar_canvas = canvas
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview,
                                  style=self._scrollbar_style())
        canvas.configure(yscrollcommand=scrollbar.set)
        inner = tk.Frame(canvas, bg=C["bg_secondary"])
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        # Auto-hide: the scrollbar only appears while the sections overflow
        # the canvas (re-checked whenever either side changes height).
        def _sync_scrollbar():
            bbox = canvas.bbox("all")
            needed = bbox is not None and bbox[3] - bbox[1] > canvas.winfo_height()
            if needed and not scrollbar.winfo_ismapped():
                scrollbar.pack(side="right", fill="y")
            elif not needed and scrollbar.winfo_ismapped():
                scrollbar.pack_forget()
                canvas.yview_moveto(0.0)

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            _sync_scrollbar()

        def _on_canvas_configure(event):
            canvas.itemconfigure(inner_id, width=event.width)
            _sync_scrollbar()

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # One global wheel binding that only acts when the pointer is over the
        # left panel (or a child of it) — no Enter/Leave toggling edge cases.
        def _wheel(event):
            widget = event.widget if isinstance(event.widget, tk.Misc) else None
            while widget is not None:
                if widget is left:
                    canvas.yview_scroll(int(-event.delta / 120), "units")
                    return
                widget = widget.master
        self.bind_all("<MouseWheel>", _wheel, add="+")

        canvas.pack(side="left", fill="both", expand=True)

        self._build_monitor_section(inner)
        self._build_controls_section(inner)
        self._build_executor_section(inner)
        self._build_counters_section(inner)
        self._build_game_info_section(inner)
        self._build_sidebets_section(inner)

    def _scrollbar_style(self):
        """Arrowless flat scrollbar matching the sidebar; returns the style
        name. The native Windows theme ignores ttk color options, so the
        trough/thumb elements are borrowed from clam, which honors them."""
        style = ttk.Style(self)
        for element in ("trough", "thumb"):
            try:
                style.element_create(f"Dark.Vertical.Scrollbar.{element}",
                                     "from", "clam",
                                     f"Vertical.Scrollbar.{element}")
            except tk.TclError:
                pass  # second window in one interpreter: elements persist
        style.layout("Dark.Vertical.TScrollbar", [
            ("Dark.Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Dark.Vertical.Scrollbar.thumb", {"expand": "1"})]})])
        style.configure("Dark.Vertical.TScrollbar",
                        troughcolor=C["bg_secondary"], background=C["border"],
                        bordercolor=C["bg_secondary"],
                        lightcolor=C["border"], darkcolor=C["border"],
                        relief="flat", width=scaling.px(10))
        style.map("Dark.Vertical.TScrollbar",
                  background=[("active", C["text_secondary"]),
                              ("pressed", C["text_secondary"])])
        return "Dark.Vertical.TScrollbar"

    def _section(self, parent, title):
        frame = tk.Frame(parent, bg=C["bg_secondary"])
        frame.pack(fill=tk.X, padx=18, pady=(14, 4))
        tk.Label(frame, text=title, font=constants.FONT_SECTION,
                 bg=C["bg_secondary"], fg=C["text_primary"]).pack(anchor="w", pady=(0, 8))
        return frame

    def _button(self, parent, text, command, bg, fg="white", hover=None, tooltip=None):
        btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                        font=constants.FONT_BODY, relief="flat", cursor="hand2",
                        borderwidth=0, padx=16, pady=9, activebackground=hover or bg)
        if hover:
            btn.bind("<Enter>", lambda e: btn["state"] == "normal" and btn.config(bg=hover))
            btn.bind("<Leave>", lambda e: btn.config(bg=btn._base_bg))
        btn._base_bg = bg
        if tooltip:
            ToolTip(btn, tooltip)
        return btn

    def _build_monitor_section(self, parent):
        section = self._section(parent, "Capture source")
        self.source_kind = tk.StringVar(value="Monitor")
        self.source_combo = ttk.Combobox(section, textvariable=self.source_kind,
                                        values=["Monitor", "Window"], state="readonly")
        self.source_combo._ui_action_label = "Tipo de captura"
        self.source_combo.pack(fill=tk.X, pady=(0, 6))
        self.source_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_sources())
        self.refresh_sources_btn = self._button(section, "Refresh windows", self._refresh_sources,
                                                C["bg_canvas_soft"])
        self.refresh_sources_btn.pack(fill=tk.X, pady=(0, 6))
        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(section, textvariable=self.monitor_var,
                                          state="readonly", font=constants.FONT_BODY)
        self._monitors = list(screeninfo.get_monitors())
        self.monitor_combo["values"] = [
            f"Monitor {i + 1}: {m.width}x{m.height}" + (" (primary)" if getattr(m, "is_primary", False) else "")
            for i, m in enumerate(self._monitors)
        ]
        self.monitor_combo._ui_action_label = "Fuente de captura"
        self.monitor_combo.pack(fill=tk.X, pady=(0, 8))
        self.monitor_combo.bind("<<ComboboxSelected>>", lambda e: self._confirm_monitor())

        self.monitor_status = tk.Label(section, text="No monitor confirmed", font=constants.FONT_SMALL,
                                       bg=C["bg_secondary"], fg=C["text_secondary"],
                                       justify=tk.LEFT, anchor="w", wraplength=240)
        self.monitor_status.pack(fill=tk.X, pady=(0, 4))
        self.calibration_action_btn = self._button(
            section, "Calibrate Regions", self._calibrate_regions, C["bg_canvas_soft"],
            tooltip="Abrir las zonas de la fuente seleccionada para esta resolucion")
        self.calibration_action_btn.pack(fill=tk.X, pady=(0, 4))
        section.bind("<Configure>", lambda event: self.monitor_status.config(
            wraplength=max(80, event.width - 16)), add="+")
        row = tk.Frame(section, bg=C["bg_secondary"])
        row.pack(fill=tk.X, pady=(6, 0))
        self.round_enabled = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="Cartel de ronda", variable=self.round_enabled,
                       command=self._toggle_round_overlay, bg=C["bg_secondary"],
                       fg=C["text_primary"], selectcolor=C["bg_secondary"],
                       font=constants.FONT_BODY).pack(side=tk.LEFT)
        ttk.Button(row, text="Ajustes", command=self._round_overlay_options).pack(side=tk.RIGHT)


    def _build_controls_section(self, parent):
        section = self._section(parent, "Controls")

        self.mode_var = tk.StringVar(value=APP_MODE_LABELS[INDIVIDUAL])
        self._app_mode = INDIVIDUAL
        self._mode_context = None
        self.mode_combo = ttk.Combobox(section, textvariable=self.mode_var,
            values=list(MODE_BY_LABEL), state="readonly", font=constants.FONT_BODY)
        self.mode_combo.pack(fill=tk.X, pady=(0, 8))
        self.mode_combo.bind("<<ComboboxSelected>>", lambda event: self._select_mode())

        # Table profile: named region calibrations (seat/dealer + OCR rects)
        # per casino table. Selecting one swaps every region live.
        row = tk.Frame(section, bg=C["bg_secondary"])
        row.pack(fill=tk.X)
        tk.Label(row, text="Table profile", font=constants.FONT_BODY,
                 bg=C["bg_secondary"], fg=C["text_secondary"]
                 ).pack(anchor="w")
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(section, textvariable=self.profile_var,
                                          state="readonly", font=constants.FONT_BODY)
        self.profile_combo._ui_action_label = "Perfil de regiones"
        self.profile_combo.pack(fill=tk.X, pady=(0, 8))
        self.profile_combo.bind("<<ComboboxSelected>>",
                                lambda e: self._select_profile())
        ToolTip(self.profile_combo,
                "Saved region calibrations per casino table — switching swaps "
                "the seat, dealer and OCR regions. Save a new one from "
                "Calibrate Regions.")
        self._profile_by_label = {}
        self._refresh_profiles()

        self.start_btn = self._button(
            section, "▶  Start Detection", self._toggle_detection,
            C["success"], hover=C["success_hover"],
            tooltip="Start/stop watching the selected monitor for cards")
        self.start_btn.pack(fill=tk.X, pady=4)
        self.start_btn.config(state="disabled")

        self._button(section, "↺  New Round", self._new_round, C["warning"],
                     fg=C["text_primary"],
                     tooltip="Clear all seats and the dealer card (shoe count is kept)"
                     ).pack(fill=tk.X, pady=4)
        self._button(section, "🂠  New Shoe", self._new_shoe, C["accent"], hover=C["accent_hover"],
                     tooltip="Reset the running count and per-card totals after a shuffle"
                     ).pack(fill=tk.X, pady=4)
        self.regions_btn = self._button(
            section, "⬚  Preview Regions", self._preview_regions, C["bg_canvas_soft"],
            tooltip="Capture the table and show the detected cards + seat regions")
        self.regions_btn.pack(fill=tk.X, pady=4)
        self.regions_btn.config(state="normal")
        self.calibrate_btn = self._button(
            section, "✥  Calibrate Regions", self._calibrate_regions, C["bg_canvas_soft"],
            tooltip="Drag the seat polygons and dealer area over a live screenshot")
        self.calibrate_btn.pack(fill=tk.X, pady=4)
        self.calibrate_btn.config(state="disabled")
        self.advanced_tools_open = tk.BooleanVar(value=False)
        self.advanced_tools_btn = ttk.Button(section, text="Advanced tools ▸", command=self._toggle_advanced_tools)
        self.advanced_tools_btn.pack(fill=tk.X, pady=(8, 2))
        self.advanced_tools_frame = tk.Frame(section, bg=C["bg_secondary"])
        self.advanced_tools_frame.pack(fill=tk.X)
        self.advanced_tools_frame.pack_forget()
        self.ocr_btn = self._button(
            self.advanced_tools_frame, "🔡  OCR Regions", self._calibrate_ocr, C["bg_canvas_soft"],
            tooltip="Mark the balance / bet / result / timer areas to read "
                    "from the screen")
        self.ocr_btn.pack(fill=tk.X, pady=4)
        self.ocr_btn.config(state="disabled")
        self.controls_btn = self._button(
            self.advanced_tools_frame, "🎛  Capture Controls", self._calibrate_controls,
            C["bg_canvas_soft"],
            tooltip="Capture the Hit/Stand/Double/Split buttons and the bet "
                    "spot as templates — powers phase & turn detection")
        self.controls_btn.pack(fill=tk.X, pady=4)
        self.controls_btn.config(state="disabled")
        self.anchors_btn = self._button(
            self.advanced_tools_frame, "⚓  Capture Anchors", self._calibrate_anchors,
            C["bg_canvas_soft"],
            tooltip="Capture 2-3 stable UI elements (logo, chip tray, menu) "
                    "— maps this calibration to any resolution or window "
                    "position")
        self.anchors_btn.pack(fill=tk.X, pady=4)
        self.anchors_btn.config(state="disabled")

    def _toggle_advanced_tools(self):
        opened = not self.advanced_tools_open.get()
        self.advanced_tools_open.set(opened)
        if opened:
            self.advanced_tools_frame.pack(fill=tk.X, pady=(0, 4))
            self.advanced_tools_btn.config(text="Advanced tools ▾")
        else:
            self.advanced_tools_frame.pack_forget()
            self.advanced_tools_btn.config(text="Advanced tools ▸")

    def _build_executor_section(self, parent):
        """Ghost-mode executor controls (V4 Feature 2). Ghost draws where it
        WOULD click and logs the would-click match rate; Assist additionally
        fires on the F8 confirm key — after the §6 ghost gate, never before."""
        # Keep the existing implementation available for future review while
        # the primary interface remains advisory and the executor stays off.
        if self.executor.mode != "off":
            self.executor.set_mode("off")
        section = self._section(parent, "Executor (ghost / assist)")
        self.exec_mode_var = tk.StringVar()
        self._exec_mode_by_label = {"Off": "off", "Ghost (plan only)": "ghost",
                                    "Assist (F8 fires)": "assist"}
        combo = ttk.Combobox(section, textvariable=self.exec_mode_var,
                             state="readonly", font=constants.FONT_BODY,
                             values=list(self._exec_mode_by_label))
        labels = {v: k for k, v in self._exec_mode_by_label.items()}
        combo.set(labels.get(self.executor.mode, "Off"))
        combo.pack(fill=tk.X, pady=(0, 4))
        if not self.executor.allowed:
            combo.config(state="disabled")
        combo.bind("<<ComboboxSelected>>", lambda e: self._set_executor_mode())
        ToolTip(combo,
                "Ghost: computes and draws the click it WOULD make, clicks "
                "nothing, logs the match rate. Assist: a single F8 press "
                "fires the planned click — arm only after a long, "
                "near-perfect ghost sample.")
        self.arm_btn = self._button(
            section, "⚪  ARM", self._toggle_arm, C["text_secondary"],
            tooltip="Master safety. Off every session start; auto-disarms "
                    "on any guard breach. F9 = kill switch, mouse in a "
                    "screen corner = abort.")
        self.arm_btn.pack(fill=tk.X, pady=4)
        self.arm_btn.config(state="disabled")
        self.exec_status_var = tk.StringVar(value="Off — advisory only.")
        tk.Label(section, textvariable=self.exec_status_var,
                 font=constants.FONT_SMALL, bg=C["bg_secondary"],
                 fg=C["text_secondary"], wraplength=240, justify="left"
                 ).pack(anchor="w")
        tk.Label(section, text="F8 confirm · F9 kill · corner = abort",
                 font=constants.FONT_SMALL, bg=C["bg_secondary"],
                 fg=C["text_secondary"]).pack(anchor="w", pady=(2, 0))
        section.pack_forget()

    def _set_executor_mode(self):
        mode = self._exec_mode_by_label.get(self.exec_mode_var.get(), "off")
        self.executor.set_mode(mode)
        mode = self.executor.mode
        self.exec_mode_var.set({v: k for k, v in self._exec_mode_by_label.items()}[mode])
        self.controller.engine.persist_settings_async()
        self.arm_btn.config(state="normal" if mode == "assist" else "disabled")
        if mode == "assist":
            self._start_hotkeys()
        else:
            self._stop_hotkeys()
            self._hide_marker()
        if mode == "off":
            self.exec_status_var.set("Off — advisory only.")

    def _refresh_executor_ui(self):
        """Immediate executor display sync — the master safety indicator
        must reflect arm/disarm THE MOMENT it changes, not at the next
        snapshot poll."""
        self._render_executor(
            self.executor.step(self.controller.engine.get_snapshot()))

    def _toggle_arm(self):
        if self.executor.armed:
            self.executor.disarm("manual disarm")
            self._refresh_executor_ui()
            return
        if not self.controller.running:
            self.exec_status_var.set("Cannot arm — start detection first.")
            return
        stats = self.executor.stats() or {}
        rate = stats.get("match_rate")
        sample = (f"{stats.get('judged', 0)} judged plans, "
                  + (f"{rate:.0%} verified" if rate is not None
                     else "no verified sample yet"))
        if not messagebox.askyesno(
                "ARM executor",
                "Assist mode clicks REAL buttons with REAL money when you "
                f"press F8.\n\nGhost telemetry this session: {sample}.\n"
                "AUTONOMY_PLAN §6: arm only after a long ghost sample with "
                "a near-perfect match rate.\n\nArm now?",
                icon="warning", parent=self):
            return
        self.executor.arm()
        self._refresh_executor_ui()

    # Hotkeys fire on a watcher thread; marshal to the Tk thread.

    def _start_hotkeys(self):
        if self._hotkeys is not None and self._hotkeys.running:
            return
        from ..logic.hotkeys import GlobalHotkeys
        self._hotkeys = GlobalHotkeys({
            int(constants.EXECUTOR.get("confirm_vk", 0x77)):
                lambda: self.after(0, self._hotkey_confirm),
            int(constants.EXECUTOR.get("kill_vk", 0x78)):
                lambda: self.after(0, self._hotkey_kill),
        })
        self._hotkeys.start()

    def _stop_hotkeys(self):
        if self._hotkeys is not None:
            self._hotkeys.stop()
            self._hotkeys = None

    def _hotkey_confirm(self):
        result = self.executor.confirm()
        self._refresh_executor_ui()
        self.exec_status_var.set(result)

    def _hotkey_kill(self):
        self.executor.kill("kill switch (F9)")
        self._refresh_executor_ui()
        self.exec_status_var.set("KILLED — disarmed (F9).")

    def _hide_marker(self):
        if self.ghost_marker is not None and self.ghost_marker.winfo_exists():
            self.ghost_marker.hide()

    def _render_executor(self, state):
        """Per-snapshot executor display sync (Tk thread)."""
        armed = state["armed"]
        text = "🔴  ARMED — F8 fires" if armed else "⚪  ARM"
        bg = C["danger"] if armed else C["text_secondary"]
        if self.arm_btn.cget("text") != text:
            self.arm_btn.config(text=text, bg=bg, activebackground=bg)
            self.arm_btn._base_bg = bg
        if state["mode"] != "off":
            status = state["status"] or "watching…"
            session = state["session"]
            if session["plans"]:
                status += (f"  ·  {session['plans']} plans, "
                           f"{session['fired']} fired, "
                           f"{session['verified']} verified")
            self.exec_status_var.set(status)
        # The executor stays disabled. Its former marker would create a
        # second floating surface over the VM.
        self._hide_marker()

    def _build_counters_section(self, parent):
        section = self._section(parent, "Cards Seen (this shoe)")
        self.counter_vars = {}
        for key in COUNTER_KEYS:
            row = tk.Frame(section, bg=C["bg_primary"])
            row.pack(fill=tk.X, pady=1)
            label = "10 / J / Q / K" if key == "10" else key
            tk.Label(row, text=label, font=constants.FONT_BODY, width=11, anchor="w",
                     bg=C["bg_primary"], fg=C["text_primary"]).pack(side=tk.LEFT, padx=(8, 0))
            var = tk.StringVar(value="0")
            tk.Label(row, textvariable=var, font=constants.FONT_BODY_BOLD, width=4,
                     bg=C["bg_primary"], fg=C["success"]).pack(side=tk.LEFT)
            for text, delta, color in (("−", -1, C["danger"]), ("+", 1, C["success"])):
                tk.Button(row, text=text, width=2, relief="flat", cursor="hand2",
                          bg=color, fg="white", font=constants.FONT_BODY_BOLD,
                          command=lambda k=key, d=delta: self._adjust_counter(k, d)
                          ).pack(side=tk.LEFT, padx=2, pady=2)
            self.counter_vars[key] = var

    def _build_game_info_section(self, parent):
        section = self._section(parent, "Game Info")
        self.info_vars = {}
        for key, label in [("round", "Round"), ("phase", "Phase"),
                           ("running", "Running count"),
                           ("true", "True count"), ("decks", "Decks remaining"),
                           ("seen", "Cards seen"), ("bet", "Bet hint"),
                           ("behind", "Bet behind"), ("pnl", "Session P&L")]:
            row = tk.Frame(section, bg=C["bg_secondary"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, font=constants.FONT_BODY, width=14, anchor="w",
                     bg=C["bg_secondary"], fg=C["text_secondary"]).pack(side=tk.LEFT)
            var = tk.StringVar(value="—")
            tk.Label(row, textvariable=var, font=constants.FONT_BODY_BOLD, anchor="w",
                     bg=C["bg_secondary"], fg=C["text_primary"], wraplength=150,
                     justify="left").pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.info_vars[key] = var

        tk.Label(section, text="Card suit readings are not reliable; use ranks and counts as guidance.",
                 font=constants.FONT_SMALL, bg=C["bg_secondary"],
                 fg=C["text_secondary"], wraplength=240, justify="left"
                 ).pack(anchor="w", pady=(3, 0))

        # Editable bankroll feeds the fractional-Kelly bet suggestion.
        row = tk.Frame(section, bg=C["bg_secondary"])
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text="Bankroll (€)", font=constants.FONT_BODY, width=14,
                 anchor="w", bg=C["bg_secondary"], fg=C["text_secondary"]
                 ).pack(side=tk.LEFT)
        self.bankroll_var = tk.StringVar(value=f"{constants.BETTING['bankroll']:.10g}")
        entry = tk.Entry(row, textvariable=self.bankroll_var, width=10,
                         font=constants.FONT_BODY_BOLD)
        entry.pack(side=tk.LEFT)
        attach_numeric_entry(entry)
        entry.bind("<Return>", lambda e: self._set_bankroll())
        entry.bind("<FocusOut>", lambda e: self._set_bankroll())
        self.bankroll_entry = entry
        self._synced_bankroll = constants.BETTING["bankroll"]

        # The bet actually placed per owned seat — settlement converts the
        # round's units into euros with this (defaults to the base bet).
        row = tk.Frame(section, bg=C["bg_secondary"])
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text="Bet placed (€)", font=constants.FONT_BODY, width=14,
                 anchor="w", bg=C["bg_secondary"], fg=C["text_secondary"]
                 ).pack(side=tk.LEFT)
        self.bet_placed_var = tk.StringVar(value=f"{constants.BASE_BET:.10g}")
        bet_entry = tk.Entry(row, textvariable=self.bet_placed_var, width=10,
                             font=constants.FONT_BODY_BOLD)
        bet_entry.pack(side=tk.LEFT)
        attach_numeric_entry(bet_entry)
        bet_entry.bind("<Return>", lambda e: self._set_bet_placed())
        bet_entry.bind("<FocusOut>", lambda e: self._set_bet_placed())
        self.bet_entry = bet_entry
        self._synced_bet = float(constants.BASE_BET)
        tk.Label(section, text="Click a seat's name on the table to mark it as"
                               " yours — only owned seats settle into the P&L.",
                 font=constants.FONT_SMALL, bg=C["bg_secondary"],
                 fg=C["text_secondary"], wraplength=240, justify="left"
                 ).pack(anchor="w", pady=(4, 0))

    def _build_sidebets_section(self, parent):
        """Live pre-deal EV per enabled side bet; green when the bet is +EV
        (with a Kelly-sized stake suggestion). The € entry is the stake you
        actually place per owned seat — settlement books it into the P&L.
        Rows exist for every known bet and show/hide with the settings."""
        section = self._section(parent, "Side Bets (EV per unit · € stake)")
        self.sidebet_rows = {}
        self.sidebet_stake_vars = {}
        for key, cfg in constants.SIDE_BETS.items():
            row = tk.Frame(section, bg=C["bg_secondary"])
            if cfg.get("enabled"):
                row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=cfg.get("label", key), font=constants.FONT_BODY,
                     width=13, anchor="w", bg=C["bg_secondary"],
                     fg=C["text_secondary"]).pack(side=tk.LEFT)
            stake_var = tk.StringVar(value=f"{cfg.get('stake') or 0:.10g}")
            stake = tk.Entry(row, textvariable=stake_var, width=5,
                             font=constants.FONT_BODY)
            stake.pack(side=tk.RIGHT)
            attach_numeric_entry(stake)
            stake.bind("<Return>", lambda e, k=key: self._set_side_bet_stake(k))
            stake.bind("<FocusOut>", lambda e, k=key: self._set_side_bet_stake(k))
            ToolTip(stake, "Your stake on this bet per owned seat "
                           "(0 = not playing it)")
            self.sidebet_stake_vars[key] = stake_var
            var = tk.StringVar(value="—")
            lbl = tk.Label(row, textvariable=var, font=constants.FONT_BODY_BOLD,
                           anchor="w", bg=C["bg_secondary"], fg=C["text_primary"])
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.sidebet_rows[key] = (row, var, lbl)

    def _set_side_bet_stake(self, key):
        var = self.sidebet_stake_vars[key]
        try:
            value = float(var.get().replace(",", "."))
        except ValueError:
            var.set(f"{constants.SIDE_BETS[key].get('stake') or 0:.10g}")
            return
        if value > 10_000_000:
            value = 10_000_000.0
            var.set(f"{value:.10g}")
        if value >= 0:
            self.controller.engine.set_side_bet_stake(key, value)

    # ------------------------------------------------------------ table/status

    def _build_table(self):
        wrapper = tk.Frame(self, bg=C["bg_primary"])
        wrapper.grid(row=1, column=1, sticky="nsew", padx=12, pady=12)
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)
        self.table = TableView(wrapper, self._on_card_click, self._on_dealer_click,
                               self._on_split_click, self._on_seat_name_click,
                               self._on_dealer_extra_click,
                               on_advice_click=self._on_advice_click)
        self.table.canvas.grid(row=0, column=0, sticky="nsew")
        # Clicking the felt parks keyboard focus back on the window — an
        # Entry otherwise keeps focus for the whole session after one click
        # (labels/canvas never take it), which froze the money-entry sync.
        self.table.canvas.bind("<Button-1>", lambda e: self.focus_set(), add="+")

    def _build_status_bar(self):
        bar = tk.Frame(self, bg=C["bg_secondary"],
                       highlightbackground=C["border"], highlightthickness=1, height=34)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.status_var, font=constants.FONT_SMALL,
                 bg=C["bg_secondary"], fg=C["text_secondary"], anchor="w", padx=12
                 ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.metrics_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.metrics_var, font=constants.FONT_SMALL,
                 bg=C["bg_secondary"], fg=C["text_secondary"], anchor="e", padx=12
                 ).pack(side=tk.RIGHT)

        # Reshuffle badge + mid-shoe paytable warning: created once here,
        # shown/hidden by _render on every snapshot poll (idempotent).
        self.paytable_warning = tk.Label(
            bar, text="⚠ Paytable changed mid-shoe", font=constants.FONT_SMALL,
            bg=C["bg_secondary"], fg=C["danger"], padx=8)
        self.reshuffle_badge = tk.Frame(bar, bg=C["warning"])
        tk.Label(self.reshuffle_badge, text="♻ Reshuffle detected",
                 font=constants.FONT_SMALL, bg=C["warning"], fg=C["bg_primary"],
                 padx=6).pack(side=tk.LEFT)
        tk.Button(self.reshuffle_badge, text="×", command=self._dismiss_reshuffle,
                  bg=C["warning"], fg=C["bg_primary"], relief="flat", bd=0,
                  cursor="hand2", padx=4, font=constants.FONT_BODY_BOLD
                  ).pack(side=tk.LEFT)
        # Session-guardrail banner (V3 E5): breached plan, no dismiss —
        # it stands until the limits change or the session ends.
        self.guardrail_banner = tk.Label(
            bar, text="", font=constants.FONT_BODY_BOLD, bg=C["danger"],
            fg="white", padx=10)
        # Anchor-drift badge (V3 E3): calibration may be misaligned; the
        # button re-solves the transform on the next worker cycle.
        self.anchor_badge = tk.Frame(bar, bg=C["danger"])
        tk.Label(self.anchor_badge, text="⚓ Anchor drift",
                 font=constants.FONT_SMALL, bg=C["danger"], fg="white",
                 padx=6).pack(side=tk.LEFT)
        tk.Button(self.anchor_badge, text="Re-anchor",
                  command=self._reanchor, bg=C["danger"], fg="white",
                  relief="flat", bd=0, cursor="hand2", padx=6,
                  font=constants.FONT_SMALL).pack(side=tk.LEFT)

    def _dismiss_reshuffle(self):
        self.controller.engine.dismiss_reshuffle_badge()

    def _reanchor(self):
        self.controller.engine.request_anchor_resolve()
        self.set_status("Re-anchoring on the next detection cycle…")

    def set_status(self, message, error=False):
        self.status_var.set(message)
        self.ui_actions.status(message, error)

    # --------------------------------------------------------------- actions

    def _configuration_busy(self):
        return bool(getattr(self.controller, "configuration_busy", self.controller.running))

    def _selected_app_mode(self) -> str:
        label = self.mode_var.get()
        try:
            return MODE_BY_LABEL[label]
        except KeyError:
            raise ValueError(f"Selecci\u00f3n de modo inv\u00e1lida: {label}") from None

    def _sync_mode_context(self, profile, source):
        from ..logic import region_profiles
        from ..logic.window_capture import source_descriptor
        resolution = (source.width, source.height)
        descriptor = source_descriptor(source, self.source_kind.get())
        context = (profile, resolution, tuple(sorted(descriptor.items())),
                   getattr(source, "hwnd", None), getattr(source, "x", None), getattr(source, "y", None))
        if context != getattr(self, "_mode_context", None):
            mode = region_profiles.layout_mode(resolution, profile=profile)
            self.mode_var.set(APP_MODE_LABELS[mode])
            self._app_mode = mode
            self._mode_context = context

    def _select_mode(self):
        previous = getattr(self, "_app_mode", INDIVIDUAL)
        if ModernBlackjackGUI._configuration_busy(self):
            self.mode_var.set(APP_MODE_LABELS[previous])
            self.set_status("El trabajador sigue activo o terminando; no se puede cambiar el modo.", error=True)
            return
        try:
            mode = self._selected_app_mode()
        except ValueError as exc:
            self.mode_var.set(APP_MODE_LABELS[previous])
            self.set_status(str(exc), error=True)
            return
        self._app_mode = mode
        self._source_configured = False
        self.start_btn.config(state="disabled")
        if self.monitor is not None:
            from ..logic import region_profiles
            try:
                self._sync_mode_context(region_profiles.active_name(), self.monitor)
            except (OSError, ValueError) as exc:
                self.set_status(str(exc), error=True)
                return
            # A manual selection wins over the saved selection in this context.
            self.mode_var.set(APP_MODE_LABELS[mode])
            self._app_mode = mode
            if self._confirm_monitor():
                self.set_status(f"Modo {APP_MODE_LABELS[mode]} seleccionado. Calibraci\u00f3n aplicada.")
        else:
            self.set_status(f"Modo {APP_MODE_LABELS[mode]} seleccionado. Seleccion\u00e1 una fuente para calibrar.")

    def _set_configuration_controls(self, busy):
        for name in ("mode_combo", "profile_combo", "source_combo", "monitor_combo"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.config(state="disabled" if busy else "readonly")
        for name in ("refresh_sources_btn", "calibrate_btn", "ocr_btn", "controls_btn", "anchors_btn"):
            widget = getattr(self, name, None)
            if widget is not None:
                allowed = name == "refresh_sources_btn" or (
                    self.monitor is not None and (name == "calibrate_btn" or self._source_configured))
                widget.config(state="normal" if not busy and allowed else "disabled")

    def _refresh_sources(self):
        if ModernBlackjackGUI._configuration_busy(self):
            self.source_kind.set(getattr(self, "_source_kind_selection", self.source_kind.get()))
            self.ui_actions.blocked("Detené la detección antes de cambiar la fuente.")
            return
        self._source_kind_selection = self.source_kind.get()
        self._source_configured = False
        if self.source_kind.get() == "Window":
            from ..logic.window_capture import list_windows
            try:
                self._monitors = list_windows()
            except Exception as exc:
                self.set_status(f"Cannot list windows: {exc}", error=True)
                return
            from ..logic.window_capture import window_display_labels
            labels = window_display_labels(self._monitors)
        else:
            self._monitors = list(screeninfo.get_monitors())
            labels = [f"Monitor {i + 1}: {m.width}x{m.height}"
                      for i, m in enumerate(self._monitors)]
        self.monitor_combo["values"] = labels
        self.monitor_combo.set("")
        self.monitor = None
        for button in (self.start_btn, self.calibrate_btn,
                       self.ocr_btn, self.controls_btn, self.anchors_btn):
            button.config(state="disabled")
        self.monitor_status.config(text="Select a window" if self.source_kind.get() == "Window"
                                   else "Select a monitor", fg=C["text_secondary"])
        self.set_status("Window capture: keep the window restored; it may stay in the background.")

    def _auto_select_monitor(self):
        # Do not silently fall back to a physical monitor when this profile
        # was calibrated against a window. Let the user choose/reselect it.
        try:
            from ..logic import region_profiles
            contexts = region_profiles.source_contexts()
            if any(item.get("kind") == "window" for item in contexts.values()):
                self.source_kind.set("Window")
                self._refresh_sources()
                self.set_status("This profile references a window source. Select that window or refresh the list.")
                return
        except (OSError, AttributeError, TypeError):
            pass
        if self._monitors:
            primary = next((i for i, m in enumerate(self._monitors)
                            if getattr(m, "is_primary", False)), 0)
            self.monitor_combo.current(primary)
            self._confirm_monitor()

    def _confirm_monitor(self):
        if ModernBlackjackGUI._configuration_busy(self):
            self.set_status("El trabajador sigue activo o terminando; configuraci\u00f3n bloqueada.", error=True)
            return False
        idx = self.monitor_combo.current()
        if idx < 0 or idx >= len(self._monitors):
            self._source_configured = False
            self.monitor = None
            for button in (self.start_btn, self.calibrate_btn, self.regions_btn,
                           self.ocr_btn, self.controls_btn, self.anchors_btn):
                button.config(state="disabled")
            self.monitor_status.config(text="Seleccioná una fuente de captura válida.", fg=C["danger"])
            if self.ui_actions.current is not None:
                self.ui_actions.blocked("Seleccioná una fuente de captura de la lista.")
            return False
        self._source_configured = False
        selected = self._monitors[idx]
        if self.source_kind.get() == "Window":
            try:
                selected = selected.ready_source()
            except RuntimeError as exc:
                self.monitor = None
                for button in (self.start_btn, self.calibrate_btn,
                               self.ocr_btn, self.controls_btn, self.anchors_btn):
                    button.config(state="disabled")
                self.monitor_status.config(text=str(exc), fg=C["danger"])
                self.set_status(str(exc), error=True)
                return False
        self.monitor = selected
        self.start_btn.config(state="disabled")
        self.calibrate_btn.config(state="normal")
        source_warning = ""
        source_review = False
        try:
            from ..logic import region_profiles
            from ..logic.window_capture import source_descriptor, source_review_required
            profile = region_profiles.active_name()
            self._sync_mode_context(profile, selected)
            selected_mode = self._selected_app_mode()
            resolution = (selected.width, selected.height)
            available = region_profiles.has_card_layout(resolution, selected_mode, profile=profile)
            if not available:
                # Explicit load distinguishes corrupt geometry from missing geometry.
                region_profiles.get_card_layout(resolution, selected_mode, profile=profile)
                raise ValueError(f'El perfil "{profile}" necesita calibraci\u00f3n '
                                 f'{APP_MODE_LABELS[selected_mode]} para {resolution[0]}x{resolution[1]}. '
                                 'Us\u00e1 Calibrate Regions.')
            reference = region_profiles.get_source_context(resolution, profile=profile)
            descriptor = source_descriptor(selected, self.source_kind.get())
            ambiguous = (reference and self.source_kind.get() == "Window"
                         and sum(source_descriptor(candidate, "Window") == descriptor
                                 for candidate in self._monitors) > 1)
            if getattr(self, "_reviewed_source_descriptor", None) == descriptor:
                ambiguous = False  # explicit editor save in this session
            source_review = source_review_required(
                reference, descriptor, ambiguous=ambiguous,
                has_calibration=region_profiles.has_calibration(
                    (selected.width, selected.height)))
            if reference and reference != descriptor:
                source_warning = " · Fuente distinta a la usada al calibrar; revisá las zonas antes de iniciar."
            elif ambiguous:
                source_warning = " · Hay varias ventanas compatibles; comprobá que seleccionaste la correcta."
        except (OSError, ValueError, RuntimeError) as exc:
            self.regions_btn.config(state="disabled")
            for button in (self.ocr_btn, self.controls_btn, self.anchors_btn):
                button.config(state="disabled")
            self.monitor_status.config(text=str(exc), fg=C["danger"])
            self.set_status(str(exc), error=True)
            return False
        self.monitor = selected
        self._source_review_required = bool(source_review)
        if source_review:
            self.regions_btn.config(state="normal")
            self.calibrate_btn.config(state="normal")
            self.start_btn.config(state="disabled")
            for button in (self.ocr_btn, self.controls_btn, self.anchors_btn):
                button.config(state="disabled")
            self.monitor_status.config(
                text=f"{selected.width}x{selected.height} — revisar calibración de fuente.{source_warning}",
                fg=C["warning"])
            self.set_status("Detection blocked until Calibrate Regions is saved for this selected source.", error=True)
            self.ui_actions.record("SOURCE_REVIEW_REQUIRED", "Fuente incompatible o ambigua; calibración requiere revisión explícita.",
                                   level="WARNING", details=self.ui_actions.context())
            return False
        try:
            self.controller.set_monitor(self.monitor, mode=selected_mode, profile=profile)
        except (ValueError, OSError, RuntimeError) as exc:
            self.start_btn.config(state="disabled")
            self.regions_btn.config(state="disabled")
            self.calibrate_btn.config(state="normal")
            for button in (self.ocr_btn, self.controls_btn, self.anchors_btn):
                button.config(state="disabled")
            self.monitor_status.config(text=(
                f"{self.monitor.width}x{self.monitor.height} - Calibración pendiente\n"
                f"{exc}\nUsá Calibrate Regions para guardar las zonas de esta fuente."),
                fg=C["danger"])
            self.set_status(str(exc), error=True)
            self.ui_actions.record("SOURCE_NEEDS_CALIBRATION",
                "Fuente seleccionada; detección bloqueada hasta calibrar. El editor puede usar esta fuente.",
                level="WARNING", details=self.ui_actions.context())
            return False
        status = f"Using {self.source_kind.get().lower()} {idx + 1} — {self.monitor.width}x{self.monitor.height}{source_warning}"
        from ..logic.ocr import OCR_AVAILABLE
        if (OCR_AVAILABLE and constants.OCR.get("enabled")
                and not self.controller.engine.ocr_calibrated):
            # The region file is keyed by exact resolution — without this
            # hint a monitor switch silently disables balance/bet OCR.
            status += " · OCR not calibrated for this resolution"
        self.monitor_status.config(text=status, fg=C["success"])
        self.start_btn.config(state="normal")
        self.regions_btn.config(state="normal")
        self.calibrate_btn.config(state="normal")
        self.ocr_btn.config(state="normal")
        self.controls_btn.config(state="normal")
        self.anchors_btn.config(state="normal")
        self.set_status(f"{self.source_kind.get()} selected ({self.monitor.width}x{self.monitor.height}). "
                        + ("OCR coordinates follow this window. Keep it restored."
                           if self.source_kind.get() == "Window" else ""))
        self._source_configured = True
        self._applied_mode_context = (profile, selected_mode, resolution)
        self.ui_actions.record("SOURCE_CONFIGURED", "Fuente y perfil aplicados al motor.",
                               details=self.ui_actions.context())
        return True

    def _refresh_profiles(self):
        """Rebuild the table-profile dropdown — '<name> — <saved date>'."""
        from ..logic import region_profiles
        active = region_profiles.active_name()
        labels, self._profile_by_label, current = [], {}, 0
        for i, item in enumerate(region_profiles.list_profiles()):
            date = (item["saved"] or "")[:10]
            from ..logic.region_profiles import calibration_resolution_summary
            card_resolutions, ocr_resolutions = calibration_resolution_summary(item)
            cards = ", ".join(card_resolutions)
            ocr = ", ".join(ocr_resolutions)
            detail = f" — {cards}" if cards else " — sin calibración de cartas"
            if ocr and ocr != cards:
                detail += f" · OCR {ocr}"
            label = f"{item['name']}{detail}" + (f" · {date}" if date else "")
            labels.append(label)
            self._profile_by_label[label] = item["name"]
            if item["name"] == active:
                current = i
        self.profile_combo["values"] = labels
        self.profile_combo.current(current)

    def _select_profile(self):
        from ..logic import region_profiles
        if ModernBlackjackGUI._configuration_busy(self):
            self._refresh_profiles()
            self.set_status("El trabajador sigue activo o terminando; no se puede cambiar el perfil.", error=True)
            return
        name = self._profile_by_label.get(self.profile_var.get())
        if not name:
            return
        try:
            if self.monitor is not None:
                mode = region_profiles.layout_mode((self.monitor.width, self.monitor.height), profile=name)
            else:
                mode = INDIVIDUAL
            if not region_profiles.set_active(name):
                return
            self.mode_var.set(APP_MODE_LABELS[mode])
            self._app_mode = mode
            self._mode_context = None
            self._source_configured = False
            self.start_btn.config(state="disabled")
            if self.monitor is not None:
                if self._confirm_monitor():
                    self.set_status(f'Perfil "{name}" aplicado al motor.')
            else:
                self.set_status(f'Perfil "{name}" seleccionado; seleccion\u00e1 una fuente y verific\u00e1 la calibraci\u00f3n.')
        except (OSError, ValueError, RuntimeError) as exc:
            self._source_configured = False
            self.start_btn.config(state="disabled")
            self.set_status(str(exc), error=True)

    def _toggle_detection(self):
        if self.controller.running:
            self.controller.stop()
            # No fresh snapshots while stopped — acting on a stale phase
            # would be a blind click.
            self.executor.disarm("detection stopped")
            self._hide_marker()
            self.start_btn.config(text="▶  Start Detection", bg=C["success"],
                                  activebackground=C["success_hover"])
            self.start_btn._base_bg = C["success"]
            self._configuration_was_busy = True
            self._set_configuration_controls(True)
            self.start_btn.config(state="disabled")
            self.set_status("Detection stopped.")
            self._detection_action = (self.ui_actions.pending(
                "Parada solicitada; esperando que termine el hilo de detección."), "stopped")
            return
        if ModernBlackjackGUI._configuration_busy(self):
            self.set_status("Esper\u00e1 a que termine el trabajador.", error=True)
            return
        if self.monitor is None:
            messagebox.showerror("No monitor", "Confirm a monitor first.")
            return
        if not self._source_configured:
            self.ui_actions.blocked("La fuente tiene una calibración pendiente. Usá Calibrate Regions antes de iniciar.")
            return
        if not self._confirm_monitor():
            return
        if self.round_enabled.get():
            self._toggle_round_overlay()
        self.controller.start()
        self._configuration_was_busy = True
        self._set_configuration_controls(True)
        self.start_btn.config(text="■  Stop Detection", bg=C["danger"],
                              activebackground=C["danger_hover"])
        self.start_btn._base_bg = C["danger"]
        # Switching monitors or table profiles mid-run would race the
        # in-flight capture cycle — and the calibrate editors can switch
        # the profile too (Save as profile), so they lock with it.
        self.monitor_combo.config(state="disabled")
        self.source_combo.config(state="disabled")
        self.refresh_sources_btn.config(state="disabled")
        self.profile_combo.config(state="disabled")
        self.calibrate_btn.config(state="disabled")
        self.ocr_btn.config(state="disabled")
        self.controls_btn.config(state="disabled")
        self.anchors_btn.config(state="disabled")
        self.set_status("Detection starting — initializing models...")
        self._detection_action = (self.ui_actions.pending(
            "Inicio solicitado; esperando confirmación del motor."), "running")

    def _new_round(self):
        self.controller.engine.new_round()
        self.set_status("Round reset.")

    def _new_shoe(self):
        if messagebox.askyesno("New shoe", "Reset the running count and all per-card totals?"):
            self.controller.engine.reset_shoe()
            self.set_status("Shoe counts reset.")

    def _adjust_counter(self, key, delta):
        before = self.controller.engine.counter.per_rank.get(key, 0)
        self.controller.engine.adjust_counter(key, delta)
        after = self.controller.engine.counter.per_rank.get(key, 0)
        self.ui_actions.record("COUNTER_ADJUSTMENT", f"Contador {key}: {before} → {after} (pedido {delta:+d}).",
                               details={"rank_bucket": key, "requested_delta": delta,
                                        "before": before, "after": after})

    def _on_card_click(self, seat_idx, slot):
        snapshot = self.controller.engine.get_snapshot()
        seat_snap = snapshot["seats"][seat_idx] if snapshot else {"cards": []}
        n_cards = len(seat_snap["cards"])
        round_at_click = snapshot["round"] if snapshot else None
        slot = min(slot, n_cards)  # 99 from the "+" button -> append
        verb = "Replace" if slot < n_cards else "Add"
        split_info = None
        if slot >= n_cards and seat_snap.get("split"):
            # Adding to a split seat: let the user pick the target hand
            # instead of the engine's count-balancing fallback. Replacing
            # (slot < n_cards) keeps the card's existing hand tag, and
            # removal is slot-based — neither needs a selector.
            hand_of = seat_snap.get("hand_of", [])
            split_info = {"is_split": True,
                          "hands": [[n for n, h in zip(seat_snap["cards"], hand_of)
                                     if h == target] for target in (0, 1)]}
        CardPicker(self, f"Player {seat_idx + 1} — {verb} card {slot + 1}",
                   self.table.card_image,
                   lambda name, hand_idx=None: self.controller.engine.replace_card(
                       seat_idx, slot, name, expected_round=round_at_click,
                       hand_index=hand_idx),
                   allow_remove=slot < n_cards, split_info=split_info)

    def _on_dealer_click(self):
        snapshot = self.controller.engine.get_snapshot()
        round_at_click = snapshot["round"] if snapshot else None
        CardPicker(self, "Dealer up-card", self.table.card_image,
                   lambda name: self.controller.engine.replace_dealer(
                       name, expected_round=round_at_click))

    def _on_dealer_extra_click(self, idx):
        """Correct a tracked dealer draw, or add one the detector missed
        (idx 99 comes from the '+' button). The round captured at picker
        open guards against a stale pick landing after an auto reset."""
        snap = self.controller.engine.get_snapshot()
        extras = (snap["dealer"].get("extras") or []) if snap else []
        round_at_click = snap["round"] if snap else None
        if idx >= len(extras):
            CardPicker(self, "Dealer draw — add missed card",
                       self.table.card_image,
                       lambda name: self.controller.engine.add_dealer_extra(
                           name, expected_round=round_at_click),
                       allow_remove=False)
        else:
            CardPicker(self, f"Dealer draw {idx + 1} — correct card",
                       self.table.card_image,
                       lambda name: self.controller.engine.replace_dealer_extra(
                           idx, name, expected_round=round_at_click),
                       allow_remove=True)

    def _preview_regions(self):
        if self._preview_busy:
            self.ui_actions.blocked("Ya hay una vista previa en preparación.")
            return
        if self._preview_source is None and self.monitor is None:
            self._open_preview_image()
            return
        self._preview_busy = True
        self.regions_btn.config(state="disabled")
        self.set_status("Generating region preview...")
        self._preview_action = self.ui_actions.pending("Preparando la vista previa; esperando resultado.")

        source = self._preview_source
        def work():
            try:
                image = build_region_preview(
                    self.controller.engine, source=source,
                    live_running=bool(self.controller.running and source is None))
                self.after(0, lambda: self._show_preview(image))
            except Exception as e:
                self.log_manager.exception("Falló la preparación de Preview Regions.",
                                           action_id=self._preview_action)
                self.after(0, lambda e=e: self._preview_failed(e))

        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, image):
        self._preview_busy = False
        self.regions_btn.config(state="normal")
        self.table.show_preview(image, on_close=lambda: self.set_status("Preview closed."), actions=[
            ("Abrir imagen", self._open_preview_image),
            ("Volver a vivo", self._preview_live),
            ("Calibrar regiones", self._calibrate_regions),
            ("OCR Regions", self._calibrate_ocr),
            ("Alinear imagen", self._align_preview_image),
        ])
        if self._preview_source:
            w, h = self._preview_source.resolution
            self.set_status(f"Image: {self._preview_source.name} · calibration {w}x{h} · does not change the count.")
        else:
            self.set_status("Live preview — cards and seat regions are outlined.")
        self.ui_actions.complete(self._preview_action, "Vista previa dibujada en la ventana principal.")
        self._preview_action = None

    def _open_preview_image(self):
        if self._preview_busy:
            self.ui_actions.blocked("Esperá a que termine la vista previa antes de abrir otra imagen.")
            return
        path = filedialog.askopenfilename(parent=self, title="Abrir captura de la mesa",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp"), ("All files", "*.*")])
        if not path:
            self.ui_actions.cancelled("Selección de imagen cancelada.")
            return
        try:
            from ..logic.image_source import ImageSource
            target = self.controller.engine.capture.resolution if self.monitor else None
            source = ImageSource.open(path, target)
            self._preview_source = source
            try:
                source.grab_bgr()
            except ValueError:
                self._align_preview_image()
                return
            self._preview_regions()
        except Exception as exc:
            self.set_status(f"Cannot open image: {exc}", error=True)

    def _align_preview_image(self):
        if self._preview_busy:
            self.set_status("Wait for the preview to finish before aligning the image.")
            return
        if self._preview_source is None:
            self.set_status("Open an image first.")
            return
        from .image_crop import ImageCrop
        ImageCrop(self, self._preview_source, self._preview_regions)

    def _preview_live(self):
        if self._preview_busy:
            return
        if self.monitor is None:
            self.set_status("Select a monitor or window to return to live capture.")
            return
        self._preview_source = None
        self._preview_regions()

    def _preview_failed(self, error):
        self._preview_busy = False
        self.regions_btn.config(state="normal")
        self.set_status(f"Region preview failed: {error}", error=True)
        self.ui_actions.complete(self._preview_action, f"Falló la vista previa: {error}", error=True)
        self._preview_action = None

    def _open_logs(self):
        if getattr(self, "logging_window", None) is not None and self.logging_window.winfo_exists():
            self.logging_window.lift()
            return
        from .logging_window import LoggingWindow
        if not self.log_manager:
            messagebox.showerror("Logs", "Logging system not initialized.")
            return
        self.logging_window = LoggingWindow(self, self.log_manager)

    # --------------------------------------------------------------- polling

    def _poll_snapshot(self):
        try:
            snapshot = self.controller.engine.get_snapshot()
            expected = getattr(self, "_applied_mode_context", None)
            if (not self._source_configured or expected is None or snapshot is None
                    or (snapshot.get("source_profile"), snapshot.get("source_mode"),
                        tuple(snapshot.get("source_resolution") or ())) != expected):
                snapshot = None
            if snapshot is not None and snapshot["seq"] != self._last_seq:
                self._last_seq = snapshot["seq"]
                self._render(snapshot)
            if self.round_overlay is not None:
                self.round_overlay.update_from_snapshot(snapshot, self.controller.running)
            # The executor ticks EVERY poll, not just on new snapshots —
            # its staleness disarm and verification deadlines must keep
            # running when the worker stalls and snapshots stop flowing.
            self._render_executor(self.executor.step(snapshot))
            self._sync_start_button()
            if self._detection_action is not None:
                target = self._detection_action[1]
                state = self.controller.state
                thread = self.controller._thread
                settled = ((target == "running" and state in ("running", "error")) or
                           (target == "stopped" and (thread is None or not thread.is_alive())))
                if settled:
                    self.ui_actions.complete(self._detection_action[0],
                        f"Detección: {state}. {self.controller.engine.last_error or ''}",
                        error=state == "error")
                    self._detection_action = None
            self._last_render_error = None
        except Exception as e:
            # A render error must never kill the update loop.
            if str(e) != getattr(self, "_last_render_error", None):
                self.log_manager.exception(f"Error actualizando la interfaz: {e}")
                self._last_render_error = str(e)
        self.after(constants.SNAPSHOT_POLL_MS, self._poll_snapshot)

    def _sync_start_button(self):
        busy = ModernBlackjackGUI._configuration_busy(self)
        was_busy = getattr(self, "_configuration_was_busy", False)
        self._configuration_was_busy = busy
        if busy:
            self._set_configuration_controls(True)
            self.start_btn.config(state="normal" if self.controller.running else "disabled")
            return
        if was_busy:
            self.executor.disarm("detection worker stopped")
            self._hide_marker()
            self.start_btn.config(text="\u25b6  Start Detection", bg=C["success"],
                                  activebackground=C["success_hover"])
            self.start_btn._base_bg = C["success"]
            if self.monitor is not None:
                self._confirm_monitor()
        self._set_configuration_controls(False)
        self.start_btn.config(state="normal" if self._source_configured else "disabled")

    def _render(self, snap):
        self._sync_money_entries(snap)
        self.table.remember_snapshot(snap)
        self.table.update(snap)
        hud = getattr(self, "hud", None)
        if hud is not None and hud.winfo_exists():
            hud.update_from_snapshot(snap)

        count = snap["count"]
        for key, var in self.counter_vars.items():
            var.set(str(count["per_rank"].get(key, 0)))

        tc = count["true"]
        self.info_vars["round"].set(str(snap["round"]))
        phase_state = snap.get("phase") or {}
        phase_text = PHASE_LABELS.get(phase_state.get("phase"),
                                      phase_state.get("phase") or "—")
        if phase_state.get("timer_s") is not None:
            phase_text += f" · {phase_state['timer_s']}s"
        disc = phase_state.get("discipline") or {}
        if disc.get("checked"):
            phase_text += f" · played book {disc['matched']}/{disc['checked']}"
        if phase_state.get("triage"):
            phase_text += " ⚠"
        self.info_vars["phase"].set(phase_text)
        self.info_vars["running"].set(f"{count['running']:+d}")
        self.info_vars["true"].set(f"{tc:+.1f}")
        self.info_vars["decks"].set(f"{count['decks_remaining']:.1f}")
        self.info_vars["seen"].set(str(count["cards_seen"]))
        self.info_vars["bet"].set(snap["bet"])
        self.info_vars["behind"].set(snap.get("bet_behind", "—"))
        pnl = snap.get("session_pnl") or {}
        if pnl.get("rounds"):
            text = f"€{pnl['eur']:+.2f} ({pnl['units']:+g}u, {pnl['rounds']} rounds)"
            if pnl.get("side_eur"):
                text += f" · side €{pnl['side_eur']:+.2f}"
            self.info_vars["pnl"].set(text)
        else:
            self.info_vars["pnl"].set("— mark a seat as yours")

        live = {item["key"]: item for item in snap.get("side_bets", [])}
        for key, (row, var, lbl) in self.sidebet_rows.items():
            item = live.get(key)
            if item is None:
                row.pack_forget()
                continue
            if not row.winfo_ismapped():
                row.pack(fill=tk.X, pady=2)
            ev = item["ev"]
            if ev is None:
                var.set("—")
                lbl.config(fg=C["text_secondary"])
            else:
                text = f"{ev * 100:+.2f}%"
                if ev > 0:
                    text += " ●"
                    suggested = item.get("stake_suggested") or 0
                    if suggested:  # Kelly-sized: tiny by construction
                        text += f" €{suggested:g}"
                var.set(text)
                lbl.config(fg=C["success"] if ev > 0 else C["text_secondary"])

        guard = snap.get("guardrails") or {}
        if guard.get("breached"):
            if self.guardrail_banner.cget("text") != guard["text"]:
                self.guardrail_banner.config(text=guard["text"])
            if not self.guardrail_banner.winfo_ismapped():
                self.guardrail_banner.pack(side=tk.RIGHT, padx=(0, 10))
        else:
            self.guardrail_banner.pack_forget()
        if (snap.get("anchors") or {}).get("drift"):
            if not self.anchor_badge.winfo_ismapped():
                self.anchor_badge.pack(side=tk.RIGHT, padx=(0, 10))
        else:
            self.anchor_badge.pack_forget()
        if snap.get("reshuffle_badge"):
            if not self.reshuffle_badge.winfo_ismapped():
                self.reshuffle_badge.pack(side=tk.RIGHT, padx=(0, 10))
        else:
            self.reshuffle_badge.pack_forget()
        if snap.get("paytable_changed_midshoe"):
            if not self.paytable_warning.winfo_ismapped():
                self.paytable_warning.pack(side=tk.RIGHT)
        else:
            self.paytable_warning.pack_forget()

        if snap["error"]:
            self.set_status(f"⚠ {snap['error']}", error=True)
        elif self.controller.running:
            activity = {"dealing": "Dealing — watching for cards",
                        "complete": "Round complete",
                        "waiting": "Waiting for cards"}.get(snap["activity"], "")
            extra = " · cutting card seen" if snap["cutting_card_seen"] else ""
            self.set_status(f"{activity}{extra}")

        m = snap["metrics"]
        model_hint = format_model_runtime_hint(snap.get("model_metadata"))
        if self.controller.running and m["cycle_ms"]:
            skipped = " · idle (frame unchanged)" if m["skipped"] else ""
            text = (f"{snap['backend']} · cycle {m['cycle_ms']:.0f} ms"
                    f" · inference {m['inference_ms']:.0f} ms{skipped}")
            text += f" · {model_hint}"
            # OCR liveness: makes "the bankroll isn't updating" diagnosable
            # at a glance — off (no regions for this monitor), waiting
            # (enabled but nothing read yet), or seconds since last read.
            if constants.OCR.get("enabled"):
                ocr_info = snap.get("ocr")
                if ocr_info is None:
                    text += " · OCR off"
                elif ocr_info.get("ts"):
                    text += f" · OCR {max(0.0, time.time() - ocr_info['ts']):.0f}s"
                else:
                    text += " · OCR waiting"
            self.metrics_var.set(text)
        elif not self.controller.running:
            self.metrics_var.set(model_hint)
        else:
            self.metrics_var.set(model_hint)

    def _sync_money_entries(self, snap):
        """Reflect engine-side bankroll / bet-placed (OCR sync, auto-settle)
        into the sidebar entries without fighting the user: skip an entry
        only while it is ACTIVELY being edited, and write only when the
        engine value moved since the last sync so a manual edit is never
        clobbered by an unchanged engine value. Setting a StringVar on an
        Entry fires no FocusOut, so this can't loop the commit handlers."""
        bankroll = snap.get("bankroll")
        if (bankroll is not None and bankroll != self._synced_bankroll
                and not self._entry_editing(self.bankroll_entry,
                                            self.bankroll_var,
                                            self._synced_bankroll)):
            self._synced_bankroll = bankroll
            self.bankroll_var.set(f"{bankroll:.10g}")
        bet = snap.get("bet_placed")
        if (bet is not None and bet != self._synced_bet
                and not self._entry_editing(self.bet_entry,
                                            self.bet_placed_var,
                                            self._synced_bet)):
            self._synced_bet = bet
            self.bet_placed_var.set(f"{bet:.10g}")

    def _entry_editing(self, entry, var, committed):
        """True only while the user is actively typing in the entry: it has
        keyboard focus AND its text no longer parses to the committed value.
        Focus alone must not block the sync — one click into an entry parks
        keyboard focus there for the rest of the session (canvas/labels
        never reclaim it), which froze the OCR sync the moment the user
        ever touched the field."""
        try:
            if self.focus_get() is not entry:
                return False
        except (KeyError, tk.TclError):  # combobox popdowns confuse focus_get
            return False
        try:
            return float(var.get().replace(",", ".")) != committed
        except ValueError:
            return True  # mid-edit text ("", "12.") — definitely typing

    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        SettingsDialog(self, on_apply=self.controller.engine.refresh_settings)

    def _on_split_click(self, seat_idx, currently_split):
        self.controller.engine.set_split(seat_idx, not currently_split)

    def _on_seat_name_click(self, seat_idx):
        self.controller.engine.set_my_seat(seat_idx)

    def _set_bet_placed(self):
        try:
            value = float(self.bet_placed_var.get().replace(",", "."))
        except ValueError:
            self.bet_placed_var.set(f"{self.controller.engine.bet_placed:.10g}")
            self._synced_bet = self.controller.engine.bet_placed
            return
        # Same bound as the bankroll: keeps every later :.10g reset free of
        # scientific notation, which the keystroke validator would reject.
        if value > 10_000_000:
            value = 10_000_000.0
            self.bet_placed_var.set(f"{value:.10g}")
        if value >= 0:
            self._synced_bet = value
            self.controller.engine.set_bet_placed(value)

    def _open_stats(self):
        store = self.controller.engine.store
        if store is None:
            self.set_status("Session store unavailable.", error=True)
            return
        from .stats_window import StatsWindow
        StatsWindow(self, store, engine=self.controller.engine)

    def _open_bankroll(self):
        from .bankroll_window import BankrollWindow
        BankrollWindow(self, self.controller.engine.store,
                       engine=self.controller.engine)

    def _toggle_round_overlay(self):
        if self.round_enabled.get():
            if (self.round_overlay is None or
                    not self.round_overlay.winfo_exists()):
                from .round_overlay import MiniTableOverlay
                self.round_overlay = MiniTableOverlay(
                    self, self._correct_overlay_card, on_close=self._on_hud_closed)
            self.hud = self.round_overlay
            self.round_overlay.update_from_snapshot(
                self.controller.engine.get_snapshot(), self.controller.running)
            self.round_overlay.ensure_on_screen()
            self.round_overlay._show_noactivate()
        elif self.round_overlay is not None:
            self.round_overlay.close()
            self.round_overlay = None
            self.hud = None
            self.controller.engine.persist_settings_async()
        if hasattr(self, "hud_nav_btn"):
            self.hud_nav_btn.config(fg=(C["accent"] if self.round_enabled.get()
                                        else C["text_primary"]))

    def _round_overlay_options(self):
        existing = getattr(self, "_round_options", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        top = self._round_options = tk.Toplevel(self)
        top.title("Cartel de ronda")
        tk.Checkbutton(top, text="Mostrar cartel", variable=self.round_enabled,
                       command=self._toggle_round_overlay).pack(anchor="w", padx=12, pady=8)
        opacity = tk.DoubleVar(value=float(constants.UI.get("round_opacity", .85))*100)
        def set_opacity(value):
            constants.UI["round_opacity"] = float(value)/100
            if self.round_overlay:
                self.round_overlay.set_opacity(float(value)/100)
        tk.Label(top, text="Opacidad").pack(anchor="w", padx=12)
        tk.Scale(top, from_=25, to=100, variable=opacity, orient="horizontal",
                 command=set_opacity, length=260).pack(padx=12)
        editing = tk.BooleanVar(value=bool(self.round_overlay and self.round_overlay.editing))
        def set_editing():
            if editing.get() and not self.round_enabled.get():
                self.round_enabled.set(True)
                self._toggle_round_overlay()
            if self.round_overlay:
                self.round_overlay.set_editing(editing.get())
        tk.Checkbutton(top, text="Ajustar posici?n (desmarcar para bloquear)",
                       variable=editing, command=set_editing).pack(padx=12, pady=8)
        def finish():
            if self.round_overlay:
                self.round_overlay.set_editing(False)
                self.round_overlay.remember_position()
            self.controller.engine.persist_settings_async()
            top.destroy()
        tk.Button(top, text="Bloquear y guardar", command=finish).pack(pady=10)
        top.protocol("WM_DELETE_WINDOW", finish)

    def _toggle_hud(self):
        self.round_enabled.set(not self.round_enabled.get())
        self._toggle_round_overlay()

    def _correct_overlay_card(self, card_id, round_id, action,
                              new_rank=None, new_suit=None, new_seat_id=None,
                              reference=None, shoe_id=None, expected_name=None):
        details = {"card_id": card_id, "expected_round": round_id, "action": action,
                   "rank": new_rank, "suit": new_suit, "seat_id": new_seat_id}
        if reference is not None:
            details.update(legacy_ref=reference, expected_shoe=shoe_id)
        self.ui_actions.record("CARD_CORRECTION_REQUEST", "Mini Table correction requested.", details=details)
        try:
            engine = self.controller.engine
            if reference is not None:
                if action not in ("set_rank", "not_a_card"):
                    self.set_status("This reference correction does not support seat or suit changes.", error=True)
                    return False
                result = engine.correct_legacy_card(
                    reference, expected_round=round_id, expected_shoe=shoe_id,
                    expected_name=expected_name, action=action, new_rank=new_rank)
            else:
                result = engine.correct_tracked_card(
                    card_id, new_rank, expected_round=round_id, action=action,
                    new_suit=new_suit, new_seat_id=new_seat_id)
            if result is False or (reference is not None and result is None):
                self.set_status("Correction rejected because the table changed or the reference is stale.", error=True)
                return False
            reason = getattr(result, "reason", "legacy reference accepted")
            self.ui_actions.record("CARD_CORRECTION_APPLIED", f"CONFIRMED: correction applied: {reason}.", details=details)
            self.set_status("Card corrected.")
            return True
        except (ValueError, RuntimeError) as error:
            self.set_status(f"Card was not corrected: {error}", error=True)
            return False
    def _on_hud_closed(self):
        """The close control hides the one overlay and clears both references."""
        self.hud = None
        self.round_overlay = None
        if hasattr(self, "round_enabled"):
            self.round_enabled.set(False)
        if hasattr(self, "hud_nav_btn"):
            self.hud_nav_btn.config(fg=C["text_primary"])

    def _open_trainer(self, deck=None):
        existing = getattr(self, "trainer_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
        else:
            from .trainer_window import TrainerWindow
            self.trainer_window = TrainerWindow(self,
                                                self.controller.engine.store)
        if deck:
            self.trainer_window.load_replay_deck(deck, label="leak drill")

    def _on_advice_click(self, seat_index):
        """Open the EV inspector for a seat's advice line (V3 F8)."""
        snap = self.controller.engine.get_snapshot()
        if not snap:
            return
        seat = next((s for s in snap["seats"] if s["index"] == seat_index),
                    None)
        if seat is None \
                or len([c for c in seat["cards"] if c and c != "-"]) < 2 \
                or not snap["dealer"]["card"]:
            self.set_status("Nothing to inspect yet — the seat needs two "
                            "cards and the dealer an up-card.")
            return
        from .inspector_window import InspectorWindow
        InspectorWindow(self, seat=seat, dealer=snap["dealer"]["card"],
                        per_rank=snap["count"].get("per_rank") or {},
                        deck_count=self.controller.engine.counter.deck_count)

    def _open_leaks(self):
        store = self.controller.engine.store
        if store is None:
            self.set_status("Session store unavailable.", error=True)
            return
        existing = getattr(self, "leaks_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        from .leaks_window import LeaksWindow
        self.leaks_window = LeaksWindow(
            self, store, open_trainer=lambda deck: self._open_trainer(deck))

    def _maybe_restore_shoe(self):
        """Offer to restore a recent mid-shoe count after a restart."""
        engine = self.controller.engine
        if engine.store is None:
            return
        try:
            info = engine.store.load_recent_shoe_state()
        except Exception:
            return
        if info is None:
            return
        state = info["state"]
        minutes = int(info["age_s"] / 60)
        if messagebox.askyesno(
                "Restore shoe?",
                f"A shoe from {minutes} min ago was found:\n"
                f"round {info['round_number']}, {state.get('cards_seen', 0)} cards seen, "
                f"running count {state.get('running_count', 0):+d}.\n\n"
                "Restore it? (Choose No after a shuffle.)"):
            engine.apply_shoe_state(info)
            self.set_status("Previous shoe restored — counts are live again.")

    def _set_bankroll(self):
        try:
            value = float(self.bankroll_var.get().replace(",", "."))
        except ValueError:
            self.bankroll_var.set(f"{constants.BETTING['bankroll']:.10g}")
            self._synced_bankroll = constants.BETTING["bankroll"]
            return
        # Keystrokes only filter syntax; the bounds clamp happens here.
        if value > 10_000_000:
            value = 10_000_000.0
            self.bankroll_var.set(f"{value:.10g}")
        if value > 0 and value != constants.BETTING["bankroll"]:
            constants.BETTING["bankroll"] = value
            self._synced_bankroll = value
            # Off the Tk thread: an AV-scanned profile dir can make even a
            # small JSON write hitch the UI when it fires on focus changes.
            self.controller.engine.persist_settings_async()
            self.controller.engine.publish_snapshot()
            self.set_status(f"Bankroll set to €{value:g} — bet ramp updated.")

    def _calibration_capture(self):
        """Editors use the selected source even if its layout failed validation."""
        if self._preview_source is not None:
            return self._preview_source
        if self.monitor is None:
            raise ValueError("Seleccioná una ventana o abrí una imagen antes de calibrar.")
        from ..logic.monitor_utils import ScreenCapture
        source = self.monitor
        if hasattr(source, "ready_source"):
            source = source.ready_source()
        capture = ScreenCapture()
        capture.set_monitor(source)
        capture.guard = self._capture_guard
        return capture

    def _store_source_reference(self, resolution):
        if self.monitor is None:
            return
        try:
            from ..logic import region_profiles
            from ..logic.window_capture import source_descriptor
            descriptor = source_descriptor(self.monitor, self.source_kind.get())
            region_profiles.set_source_context(resolution, descriptor)
            self._reviewed_source_descriptor = descriptor
        except (OSError, ValueError, TypeError):
            self.set_status("Calibration saved; source reference could not be saved.", error=True)

    def _calibrate_regions(self):
        if ModernBlackjackGUI._configuration_busy(self):
            self.ui_actions.blocked("Detené la detección antes de calibrar las regiones.")
            return
        if self.monitor is None and self._preview_source is None:
            self.ui_actions.blocked("Seleccioná una ventana o abrí una imagen antes de calibrar.")
            return
        from .region_editor import RegionEditor
        from ..logic.window_capture import source_descriptor
        calibration_source = (source_descriptor(self.monitor, self.source_kind.get())
                              if self.monitor is not None else None)

        def reload_regions(saved=True):
            from ..logic import region_profiles
            self._source_configured = False
            self.start_btn.config(state="disabled")
            self._refresh_profiles()
            name = region_profiles.active_name()
            saved_mode = region_profiles.layout_mode(capture.resolution, profile=name)
            self.mode_var.set(APP_MODE_LABELS[saved_mode])
            self._app_mode = saved_mode
            if (self._preview_source is not None or self.monitor is None
                    or (self.monitor.width, self.monitor.height) != tuple(capture.resolution)
                    or source_descriptor(self.monitor, self.source_kind.get()) != calibration_source):
                self.set_status(f'Zonas guardadas en "{name}"; no aplicadas a la fuente viva.')
                return
            self._store_source_reference(capture.resolution)
            applied = self._confirm_monitor()
            if not applied:
                self.ui_actions.record("CALIBRATION_APPLY_FAILED", "Guardado confirmado; aplicaci\u00f3n al motor fallida.", level="ERROR")
                return
            self.ui_actions.record("CALIBRATION_APPLIED", "Perfil aplicado al motor.")
            self.set_status(f'Zonas guardadas en "{name}". Perfil aplicado al motor.')

        capture = None
        try:
            existing = getattr(self, "_region_editor", None)
            if existing is not None and existing.winfo_exists():
                existing.show_editor()
                self.set_status("Editor de regiones abierto; revisá la ventana de calibración.")
                return
            self._region_editor = None
            self.set_status("Abriendo Calibrate Regions: capturando la fuente seleccionada...")
            capture = self._calibration_capture()
            from ..logic import region_profiles
            mode = self._selected_app_mode()
            self.ui_actions.record("CALIBRATION_OPEN", "Capturando el fondo para abrir el editor de regiones.",
                details={"resolution": capture.resolution, "mode": mode,
                         "profile": region_profiles.active_name(), "uses_selected_source": True})
            def saved_regions(saved=True):
                reload_regions(saved)
            editor = RegionEditor(self, capture, on_save=saved_regions, mode=mode)
            self._region_editor = editor
            editor.show_editor()
            if not editor._shown_logged:
                self.set_status(f"Editor de regiones creado para {capture.resolution[0]}x{capture.resolution[1]}. "
                                "Esperando que se muestre.")
        except Exception as e:
            self.ui_actions.record("CALIBRATION_OPEN_FAILED", str(e), level="ERROR")
            self.log_manager.exception("No se pudo abrir el editor de regiones.",
                                       action_id=self.ui_actions.current_id)
            self.set_status(f"Calibration failed: {e}", error=True)
            messagebox.showerror("No se pudo abrir Calibrate Regions", str(e), parent=self)
        finally:
            if capture is not None and hasattr(capture, "close_local"):
                capture.close_local()

    def _calibrate_controls(self):
        if self.monitor is None:
            return
        from .control_capture import ControlCaptureEditor

        def reload_controls():
            # _confirm_monitor reloads EVERY calibration kind (seat/dealer
            # regions, OCR rects, phase templates) — a "Save as profile"
            # switches the active profile, and reloading only the templates
            # would leave the engine running mixed-profile calibration.
            self._store_source_reference((self.monitor.width, self.monitor.height))
            self._confirm_monitor()
            self._refresh_profiles()  # control saves restamp the profile date
            self.set_status("Control templates saved — phase & turn "
                            "detection now watches the buttons.")

        try:
            ControlCaptureEditor(self, self.controller.engine.capture,
                                 on_save=reload_controls)
        except Exception as e:
            self.set_status(f"Control capture failed: {e}", error=True)

    def _calibrate_anchors(self):
        if self.monitor is None:
            return
        from .anchor_editor import AnchorEditor

        def reload_anchors():
            # Same full-reload chain as the other editors: set_monitor
            # queues an anchor re-solve for the next worker cycle.
            self._store_source_reference((self.monitor.width, self.monitor.height))
            self._confirm_monitor()
            self._refresh_profiles()
            self.set_status("Anchors saved — the transform is solved on the "
                            "next detection cycle.")

        AnchorEditor(self, self.controller.engine.capture,
                     on_save=reload_anchors)

    def _calibrate_ocr(self):
        if self.controller.running:
            self.set_status("Stop Detection before changing calibration.")
            return
        capture = self._preview_source or self.controller.engine.capture
        if self.monitor is None and self._preview_source is None:
            return
        from ..logic.ocr import OCR_AVAILABLE
        if not OCR_AVAILABLE and self._preview_source is None:
            self.set_status("OCR needs the winocr package: pip install winocr",
                            error=True)
            return
        from .ocr_region_editor import OcrRegionEditor

        def reload_regions():
            if self.monitor is not None:
                self._store_source_reference((self.monitor.width, self.monitor.height))
                self._confirm_monitor()   # reload + refresh the OCR hint
            self._refresh_profiles()  # OCR saves restamp the profile date
            self.set_status("OCR regions saved — balance/bet/result now read "
                            "from the screen.")

        try:
            def saved_ocr():
                reload_regions()
                if self._preview_source is not None:
                    self._preview_regions()
            OcrRegionEditor(self, capture, on_save=saved_ocr)
        except Exception as e:
            self.set_status(f"OCR calibration failed: {e}", error=True)

    def _on_close(self):
        if self.round_overlay is not None:
            self.round_overlay.close()
            self.round_overlay = None
        self._capture_guard.close()
        self._stop_hotkeys()
        self.executor.disarm("app closing")
        self.controller.stop()
        # OCR bankroll syncs persist at round end; a close mid-round must
        # not lose the latest value.
        try:
            from ..common import settings
            settings.save()
        except Exception:
            self.log_manager.exception("No se pudo guardar la configuración al cerrar.")
        for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
            pool = getattr(self.controller.engine,name,None)
            if pool is not None:
                pool.shutdown(wait=False,cancel_futures=True)
        from ..logic import ev_offload
        ev_offload.shutdown()
        # Cancel this Tk interpreter's timers before destroying their commands.
        # Includes the regular snapshot poll and overlay/calibration callbacks.
        for job in self.tk.call("after", "info"):
            self.after_cancel(job)
        self.destroy()
